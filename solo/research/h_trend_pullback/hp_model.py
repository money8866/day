# -*- coding: utf-8 -*-
"""
H-TREND-PULLBACK-01  P4：多元模型与稳健性（§35 - §43）

覆盖规格
  §35 特征集与预处理  §36 模型规格  §37 训练与交叉验证  §38 特征重要度与符号稳定性
  §39 样本外（OOS）评估  §40 Walk-Forward  §41 分年  §42 市场状态（Regime）  §43 参数邻域敏感性

样本口径（预注册，本脚本内固定，跑数中不得更改）
  · 模型样本 SAMPLE = 「Episode 内强趋势日」(in_ep==1 且 exe_5 可得)
    理由：§9-§13 的回撤质量特征只在 Episode 内有定义，在 Episode 外为 NaN，
          把它们填 0 并入全体强趋势日会稀释模型；且 E1 信号日是 SAMPLE 的子集。
  · 横截面评估在 SAMPLE 内逐日进行（axis=1 = 当日全体 SAMPLE 个股）
  · E1 信号日（is_pb==1）单列为诊断口径（事件稀疏，日横截面样本量常低于下限，故不作主判据）

预处理（§35）
  · 每个特征：逐日横截面百分位秩（average 秩 / 当日有效样本数），中心化到 (-0.5, 0.5]
  · 缺失（该日该股特征不可得）填 0 = 中性；日固定效应由秩中心化隐式消除，不再加时间虚拟变量
  · 目标：exe_5 的逐日横截面百分位秩（中心化），与特征同尺度

模型规格（§36）—— 全报不择优
  · M1_PB      = 回撤质量特征集（§16/§17/§18/§20）
  · M2_PB_CTRL = M1 + 动量与规模控制（mom20 / mom60 / total_mv / rv_20），用于检验「控制后是否仍有增量」

估计
  · 池化 OLS（岭正则 1e-6，仅为数值稳定，不改变符号判读）
  · §38 重要度 = 标准化系数（特征与目标同为秩尺度，系数可比）；符号稳定性 = 各期 / 各年重估后符号一致性
  · Walk-Forward（§40）：训练窗 = 目标年前 3 个完整年，测试 = 目标年

纪律：本脚本只做估计与评估，不设任何择优规则、不做阈值调参救援；
      失败 Gate 不得通过改阈值救回（§26 / §52 / §55）。G 编号权威定义以用户提交规格 §51/§52 为准。
"""
import os
import sys
import time
import warnings

import numpy as np
import pandas as pd
from scipy.stats import rankdata

from hp_common import (DATA, OUTD, PREREG, mklog, pload_meta, pload_day,
                       ic_stats, monotonicity)

warnings.filterwarnings('ignore', category=RuntimeWarning)
LOG = mklog('model')

HZ = tuple(PREREG['primary_horizons'])
H_MAIN = 5
PHASES = ('TRAIN', 'VALID', 'OOS', 'LIVE-LIKE')
MIN_XS = 30
RIDGE = 1e-6

# 注：since_pb 与 ep_days 在 in_ep 内恒差 1（since_pb = k - pb_start，ep_days = k - pb_start + 1），
# 秩预处理后为同一列，构成精确共线（ridge 下系数被均分，§38 符号判读失真），故只保留 ep_days（= §12 Duration）。
PB_FEATS = ['depth', 'speed', 'ep_days', 'since_high',
            'cl_ma20', 'cl_ma60', 'lo_ma20', 'lo_ma60',
            'ep_low_ma20', 'ep_low_ma60',
            'vol_ratio', 'to_ratio', 'atr_ratio', 'rv_ratio',
            'dn_vol_ratio', 'dn_day_ratio', 'rs_pb_mkt', 'rs_pb_ind']
CTRL_FEATS = ['mom20', 'mom60', 'total_mv', 'rv_20']
MODELS = {'M1_PB': PB_FEATS, 'M2_PB_CTRL': PB_FEATS + CTRL_FEATS}
PRIMARY_MODEL = 'M1_PB'


# ══════════════════════════════════════════════ 向量化横截面统计（与 P2 同口径）
# 约定：统一接收「转置面板」(NCAL, NCODES)，axis=1 为当日全体个股
def _row_ranks(A):
    return rankdata(A, axis=1, method='average', nan_policy='omit')


def _corr_rows(RS, RT, VAL, min_xs=MIN_XS):
    a = np.where(VAL, RS, np.nan)
    b = np.where(VAL, RT, np.nan)
    n = VAL.sum(1).astype(np.float64)
    da = a - np.nanmean(a, 1, keepdims=True)
    db = b - np.nanmean(b, 1, keepdims=True)
    num = np.nansum(da * db, 1)
    den = np.sqrt(np.nansum(da * da, 1) * np.nansum(db * db, 1))
    out = np.full(len(n), np.nan)
    ok = (n >= min_xs) & (den > 0)
    out[ok] = num[ok] / den[ok]
    return out


def _auc_top_rows(RS, T, VAL, top_pct=0.10, min_xs=MIN_XS):
    n = VAL.sum(1).astype(np.float64)
    thr = np.nanquantile(np.where(VAL, T, np.nan), 1.0 - top_pct, axis=1)
    Y = VAL & (T >= thr[:, None])
    P = Y.sum(1).astype(np.float64)
    N = n - P
    RY = np.where(Y, RS, 0.0).sum(1)
    out = np.full(len(n), np.nan)
    ok = (n >= min_xs) & (P > 0) & (N > 0)
    out[ok] = (RY[ok] - P[ok] * (P[ok] + 1) / 2.0) / (P[ok] * N[ok])
    return out


def _prec_top_rows(RS, T, VAL, top_pct=0.10, min_xs=MIN_XS):
    """逐日 Precision@Top10%：分数前 10% 有多少比例落在目标前 10%"""
    n = VAL.sum(1).astype(np.float64)
    thr_s = np.nanquantile(np.where(VAL, RS, np.nan), 1.0 - top_pct, axis=1)
    thr_t = np.nanquantile(np.where(VAL, T, np.nan), 1.0 - top_pct, axis=1)
    S = VAL & (RS >= thr_s[:, None])
    Y = VAL & (T >= thr_t[:, None])
    ns = S.sum(1).astype(np.float64)
    ny = (S & Y).sum(1).astype(np.float64)
    out = np.full(len(n), np.nan)
    ok = (n >= min_xs) & (ns > 0)
    out[ok] = ny[ok] / ns[ok]
    return out


def _bucket_rows(RS, T, VAL, nq=10):
    n = VAL.sum(1).astype(np.float64)
    ok = VAL & (n[:, None] >= nq * 3)
    b = np.minimum(nq - 1, ((np.where(VAL, RS - 1.0, 0.0)
                             / np.where(VAL, n[:, None], 1.0)) * nq).astype(np.int64))
    means = np.full(nq, np.nan)
    se = np.full(nq, np.nan)
    cnt = np.zeros(nq, dtype=np.int64)
    for q in range(nq):
        v = np.where(ok & (b == q), T, np.nan)
        dm = np.nanmean(v, 1)
        f = np.isfinite(dm)
        cnt[q] = int(f.sum())
        if f.sum():
            means[q] = float(dm[f].mean())
            se[q] = (float(dm[f].std(ddof=1) / np.sqrt(f.sum())) if f.sum() > 1 else np.nan)
    return means, se, cnt


def _auc_lab_rows(RS, L, VAL, min_xs=MIN_XS):
    """逐日二值标签 AUC（用于诊断「分数能否识别 E1 信号日」）"""
    n = VAL.sum(1).astype(np.float64)
    Y = VAL & (L > 0.5)
    P = Y.sum(1).astype(np.float64)
    N = n - P
    RY = np.where(Y, RS, 0.0).sum(1)
    out = np.full(len(n), np.nan)
    ok = (n >= min_xs) & (P > 0) & (N > 0)
    out[ok] = (RY[ok] - P[ok] * (P[ok] + 1) / 2.0) / (P[ok] * N[ok])
    return out


def to_panel(TD, vals, NCODES, NCAL):
    """long 表某列 -> (NCAL, NCODES) 转置面板（同一 (code,k) 唯一）"""
    P = np.full((NCAL, NCODES), np.nan, dtype=np.float32)
    v = np.asarray(vals, dtype=np.float64)
    ok = np.isfinite(v)
    P[TD['k'].values[ok], TD['code'].values[ok]] = v[ok]
    return P


def _slice_stat(series, day_ph, pi, name):
    m = (day_ph == pi) & np.isfinite(series)
    return ic_stats(series[m], name=name)


def _ols(X, y, m, feats):
    Xm = np.asarray(X[m], dtype=np.float64)
    ym = np.asarray(y[m], dtype=np.float64)
    ok = np.isfinite(ym)
    Xm, ym = Xm[ok], ym[ok]
    XtX = Xm.T @ Xm
    XtX.flat[::XtX.shape[0] + 1] += RIDGE
    beta = np.linalg.solve(XtX, Xm.T @ ym)
    return beta, int(len(ym))


def main():
    t0 = time.time()
    LOG.sep('=')
    LOG('H-TREND-PULLBACK-01  P4 多元模型与稳健性  %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    LOG('样本：Episode 内强趋势日 (in_ep==1)；主视界 h=%d；特征逐日横截面秩中心化；池化 OLS(ridge=%.0e)'
        % (H_MAIN, RIDGE))
    M = pload_meta()
    NCODES, NCAL = int(M['NCODES']), int(M['NCAL'])
    day = pload_day()
    day_ph = day['phase']
    day_regime = day['regime']

    LOG('读入 hp_trenddays.parquet ...')
    TD = pd.read_parquet(os.path.join(DATA, 'hp_trenddays.parquet'))
    LOG('   trenddays %d 行' % len(TD))

    ycol = 'exe_%d' % H_MAIN
    in_ep = TD['in_ep'].values.astype(np.int8)
    is_pb = TD['is_pb'].values.astype(np.int8)
    year = TD['year'].values
    sample = (in_ep == 1) & np.isfinite(TD[ycol].values)
    LOG('   SAMPLE(in_ep & exe_%d) %d 行；其中 E1 信号日 %d'
        % (H_MAIN, int(sample.sum()), int((sample & (is_pb == 1)).sum())))

    # ────────────────────────────── §35 预处理：逐日横截面秩 ──────────────────────────────
    allf = sorted(set(PB_FEATS) | set(CTRL_FEATS))
    LOG.sep()
    LOG('§35 预处理：%d 个特征逐日横截面百分位秩（中心化），缺失填 0' % len(allf))
    R = TD.groupby('k')[allf].rank(pct=True, method='average') - 0.5
    X = np.nan_to_num(R[allf].to_numpy(dtype=np.float32), nan=0.0,
                      posinf=0.0, neginf=0.0)
    del R
    yr = TD.groupby('k')[ycol].rank(pct=True) - 0.5
    yv = yr.to_numpy(dtype=np.float64)
    LOG('   特征矩阵 %s；目标 = 逐日横截面 exe_%d 百分位秩' % (X.shape, H_MAIN))

    # 面板：目标 / in_ep / is_pb
    TG = to_panel(TD, TD[ycol].values, NCODES, NCAL)
    EP = to_panel(TD, in_ep.astype(np.float64), NCODES, NCAL)
    PBv = to_panel(TD, is_pb.astype(np.float64), NCODES, NCAL)

    rows_eval, rows_coef, rows_wf = [], [], []

    # ══════════════════════════════════════════════ 评估公共函数
    def ev(score, tag, model, h, sel_mask=None):
        """在面板口径上评估一个分数：逐日 IC / top-decile AUC / Precision@10 / 十分位单调性"""
        SC = to_panel(TD, score, NCODES, NCAL)
        T = TG if h == H_MAIN else to_panel(TD, TD['exe_%d' % h].values, NCODES, NCAL)
        VAL = np.isfinite(SC) & np.isfinite(T) & (EP == 1)
        if sel_mask is not None:                      # 诊断口径（如仅 E1 信号日）
            VAL = VAL & sel_mask
        RS = _row_ranks(np.where(VAL, SC, np.nan))
        RT = _row_ranks(np.where(VAL, T, np.nan))
        ic = _corr_rows(RS, RT, VAL)
        au = _auc_top_rows(RS, T, VAL)
        pr = _prec_top_rows(RS, T, VAL)
        bm, bse, bcnt = _bucket_rows(RS, T, VAL)
        rho, _ = monotonicity(bm)
        st = ic_stats(ic, name='%s_%s_h%d' % (tag, model, h))
        st_a = ic_stats(au, name='%s_%s_h%d_auc' % (tag, model, h))
        st_p = ic_stats(pr, name='%s_%s_h%d_prec' % (tag, model, h))
        rec = {'tag': tag, 'model': model, 'horizon': h, 'phase': 'ALL',
               'n_obs': int(VAL.sum()), 'n_day': st['n'],
               'ic': st['mean'], 'ic_t': st['t_nw'],
               'auc': st_a['mean'], 'prec10': st_p['mean'],
               'mono_rho': rho, 'q_spread': (float(bm[9] - bm[0])
                                             if np.isfinite(bm[9]) and np.isfinite(bm[0]) else np.nan)}
        rows_eval.append(dict(rec))
        out = {'ALL': (st, st_a, st_p, rho, bm)}
        for pn, pi in zip(PHASES, (1, 2, 3, 4)):
            s1 = _slice_stat(ic, day_ph, pi, '%s_%s_h%d_ic' % (tag, model, h))
            s2 = _slice_stat(au, day_ph, pi, '%s_%s_h%d_auc' % (tag, model, h))
            s3 = _slice_stat(pr, day_ph, pi, '%s_%s_h%d_prec' % (tag, model, h))
            rows_eval.append({'tag': tag, 'model': model, 'horizon': h, 'phase': pn,
                              'n_obs': np.nan, 'n_day': s1['n'],
                              'ic': s1['mean'], 'ic_t': s1['t_nw'],
                              'auc': s2['mean'], 'prec10': s3['mean'],
                              'mono_rho': np.nan, 'q_spread': np.nan})
            out[pn] = (s1, s2, s3, np.nan, None)
        return out, bm

    # ══════════════════════════════════════════════ §36 / §37 估计（TRAIN 拟合）
    LOG.sep()
    LOG('§36/§37 估计：池化 OLS，训练期 = TRAIN(2018-2022)，样本 = SAMPLE')
    scores, betas = {}, {}
    tr_mask = sample & (day_ph[TD['k'].values] == 1)
    LOG('   训练样本 %d 行' % int(tr_mask.sum()))
    for mname, feats in MODELS.items():
        beta, nfit = _ols(X[:, [allf.index(f) for f in feats]], yv, tr_mask, feats)
        betas[mname] = beta
        sc = X[:, [allf.index(f) for f in feats]] @ beta
        scores[mname] = sc
        LOG('   [%s] 训练 %d 行，beta: %s' % (mname, nfit,
              '  '.join('%s %+.3f' % (f, b) for f, b in zip(feats, beta))))

    # 全样本拟合（用于 §38 重要度主表）
    full_mask = sample
    beta_full = {}
    for mname, feats in MODELS.items():
        b, n = _ols(X[:, [allf.index(f) for f in feats]], yv, full_mask, feats)
        beta_full[mname] = b

    # 中间产物：SAMPLE 上的 TRAIN 拟合分数（供 P5 §49 置换检验复用，避免重复拟合；非结论文档）
    pd.DataFrame({'code': TD['code'].values[sample].astype(np.int32),
                  'k': TD['k'].values[sample].astype(np.int32),
                  's_m1': scores[PRIMARY_MODEL][sample].astype(np.float32),
                  's_m2': scores['M2_PB_CTRL'][sample].astype(np.float32)}
                 ).to_parquet(os.path.join(DATA, 'hp_model_score.parquet'), index=False)
    LOG('   中间产物落盘: hp_model_score.parquet（SAMPLE 上 TRAIN 拟合分数）')

    # ══════════════════════════════════════════════ 横截面评估（主表, h=5）
    LOG.sep()
    LOG('§36/§39 横截面评估（SAMPLE = Episode 内强趋势日，逐日横截面）')
    for mname in MODELS:
        ev(scores[mname], 'TRAIN_FIT', mname, H_MAIN)
    EVAL = pd.DataFrame(rows_eval)
    E = EVAL[(EVAL['phase'] == 'ALL') & (EVAL['horizon'] == H_MAIN)]
    LOG('   %-12s %-12s %10s %8s %8s %8s %8s %8s'
        % ('model', 'fit', 'n_obs', 'IC', 'IC_t', 'AUC', 'Prec10', 'mono'))
    for _, r in E.iterrows():
        LOG('   %-12s %-12s %10d %+8.4f %+8.2f %8.4f %8.4f %+8.3f'
            % (r['model'], r['tag'], r['n_obs'], r['ic'], r['ic_t'],
               r['auc'], r['prec10'], r['mono_rho']))
    LOG('   分期 IC（%s, h=%d）:' % (PRIMARY_MODEL, H_MAIN))
    for pn in PHASES:
        r = EVAL[(EVAL['tag'] == 'TRAIN_FIT') & (EVAL['model'] == PRIMARY_MODEL)
                 & (EVAL['horizon'] == H_MAIN) & (EVAL['phase'] == pn)]
        if len(r):
            r = r.iloc[0]
            LOG('     %-10s 日=%5d  IC %+.4f  t %+.2f  AUC %.4f  Prec10 %.4f'
                % (pn, r['n_day'], r['ic'], r['ic_t'], r['auc'], r['prec10']))

    # 多视界（主模型）
    LOG('   多视界（%s, TRAIN 拟合）:' % PRIMARY_MODEL)
    mh = {}
    for h in HZ:
        o, _ = ev(scores[PRIMARY_MODEL], 'TRAIN_FIT', PRIMARY_MODEL, h)
        mh[h] = o
        LOG('     h=%-2d  IC %+.4f (t %+.2f)  AUC %.4f  Prec10 %.4f  mono %+.3f'
            % (h, o['ALL'][0]['mean'], o['ALL'][0]['t_nw'], o['ALL'][1]['mean'],
               o['ALL'][2]['mean'], o['ALL'][3]))

    # 诊断：分数能否识别 E1 信号日
    LOG.sep()
    LOG('   诊断：分数对「E1 信号日 vs 其余 Episode 内强趋势日」的区分度（AUC）')
    SCp = to_panel(TD, scores[PRIMARY_MODEL], NCODES, NCAL)
    VALd = np.isfinite(SCp) & (EP == 1)
    RSd = _row_ranks(np.where(VALd, SCp, np.nan))
    au_d = _auc_lab_rows(RSd, PBv, VALd)
    std = ic_stats(au_d, name='e1_diag')
    LOG('     AUC(E1) = %.4f (t %+.2f, 日=%d)  — 1.0=完全分离，0.5=无区分'
        % (std['mean'], std['t_nw'], std['n']))
    rows_eval.append({
        'tag': 'DIAG_E1_LABEL', 'model': PRIMARY_MODEL, 'horizon': 0, 'phase': 'ALL',
        'n_obs': int(VALd.sum()), 'n_day': std['n'], 'ic': np.nan, 'ic_t': np.nan,
        'auc': std['mean'], 'prec10': np.nan, 'mono_rho': np.nan, 'q_spread': np.nan})
    EVAL = pd.DataFrame(rows_eval)

    # ══════════════════════════════════════════════ §38 重要度与符号稳定性
    LOG.sep()
    LOG('§38 特征重要度（标准化系数）与符号稳定性（分阶段重估）')
    for mname, feats in MODELS.items():
        for f, b in zip(feats, beta_full[mname]):
            rec = {'model': mname, 'feature': f, 'beta_full': float(b)}
            for pn, pi in zip(PHASES, (1, 2, 3, 4)):
                m_p = sample & (day_ph[TD['k'].values] == pi)
                if m_p.sum() >= 500:
                    bp, _ = _ols(X[:, [allf.index(x) for x in feats]], yv, m_p, feats)
                    rec['beta_%s' % pn] = float(bp[list(feats).index(f)])
                else:
                    rec['beta_%s' % pn] = np.nan
            sg = [np.sign(rec['beta_%s' % pn]) for pn in PHASES
                  if np.isfinite(rec.get('beta_%s' % pn, np.nan))]
            rec['sign_consistency'] = (float(max(sum(1 for x in sg if x > 0),
                                                 sum(1 for x in sg if x < 0)) / len(sg))
                                       if sg else np.nan)
            rec['sign_stable'] = int(rec['sign_consistency'] == 1.0) if sg else 0
            rows_coef.append(rec)
    COEF = pd.DataFrame(rows_coef)
    LOG('   %s 系数（full / TRAIN / VALID / OOS / LIVE）' % PRIMARY_MODEL)
    for _, r in COEF[COEF['model'] == PRIMARY_MODEL].sort_values(
            'beta_full', key=lambda s: -s.abs()).iterrows():
        LOG('     %-14s %+7.4f  %+7.4f %+7.4f %+7.4f %+7.4f  一致 %.2f'
            % (r['feature'], r['beta_full'], r['beta_TRAIN'], r['beta_VALID'],
               r['beta_OOS'], r['beta_LIVE-LIKE'], r['sign_consistency']))
    ns_st = int(COEF[(COEF['model'] == PRIMARY_MODEL)]['sign_stable'].sum())
    LOG('   符号稳定特征 %d / %d' % (ns_st, len(PB_FEATS)))

    # ══════════════════════════════════════════════ §39 OOS（TRAIN+VALID 拟合 -> 2025）
    LOG.sep()
    LOG('§39 样本外：训练 = TRAIN+VALID(2018-2024)，测试 = OOS(2025)')
    oos_mask = sample & np.isin(day_ph[TD['k'].values], (1, 2))
    oos_eval = {}
    for mname, feats in MODELS.items():
        b, n = _ols(X[:, [allf.index(f) for f in feats]], yv, oos_mask, feats)
        sc = X[:, [allf.index(f) for f in feats]] @ b
        o, _ = ev(sc, 'OOS_FIT', mname, H_MAIN)
        oos_eval[mname] = o
        LOG('   [%s] 训练 %d 行 -> OOS  IC %+.4f (t %+.2f)  AUC %.4f  Prec10 %.4f  mono %+.3f'
            % (mname, n, o['OOS'][0]['mean'], o['OOS'][0]['t_nw'], o['OOS'][1]['mean'],
               o['OOS'][2]['mean'], o['ALL'][3]))

    # ══════════════════════════════════════════════ §40 Walk-Forward（训练=前 3 年）
    LOG.sep()
    LOG('§40 Walk-Forward：训练窗 = 目标年前 3 个完整年，测试 = 目标年（全报）')
    wf_ic = {}
    for ty in PREREG['year_report']:
        tr = sample & (year >= ty - 3) & (year <= ty - 1)
        te = sample & (year == ty)
        if tr.sum() < 1000 or te.sum() < 200:
            LOG('     %d  训练 %6d / 测试 %6d  样本不足，标记 NA' % (ty, tr.sum(), te.sum()))
            rows_wf.append({'test_year': ty, 'n_train': int(tr.sum()), 'n_test': int(te.sum()),
                            'n_day': 0, 'ic': np.nan, 'ic_t': np.nan, 'auc': np.nan})
            continue
        feats = MODELS[PRIMARY_MODEL]
        b, _ = _ols(X[:, [allf.index(f) for f in feats]], yv, tr, feats)
        sc = X[:, [allf.index(f) for f in feats]] @ b
        SC = to_panel(TD, sc, NCODES, NCAL)
        VAL = np.isfinite(SC) & np.isfinite(TG) & (EP == 1)
        RS = _row_ranks(np.where(VAL, SC, np.nan))
        RT = _row_ranks(np.where(VAL, TG, np.nan))
        ic = _corr_rows(RS, RT, VAL)
        au = _auc_top_rows(RS, TG, VAL)
        my = (np.asarray(day['year']) == ty)
        st = ic_stats(ic[my], name='wf_%d' % ty)
        sta = ic_stats(au[my], name='wf_%d_auc' % ty)
        wf_ic[ty] = st['mean']
        rows_wf.append({'test_year': ty, 'n_train': int(tr.sum()), 'n_test': int(te.sum()),
                        'n_day': st['n'], 'ic': st['mean'], 'ic_t': st['t_nw'],
                        'auc': sta['mean']})
        LOG('     %d  训练 %6d / 测试 %6d  日=%4d  IC %+.4f (t %+.2f)  AUC %.4f'
            % (ty, tr.sum(), te.sum(), st['n'], st['mean'], st['t_nw'], sta['mean']))
    WF = pd.DataFrame(rows_wf)
    wf_valid = WF[np.isfinite(WF['ic'])]
    wf_pos = float((wf_valid['ic'] > 0).mean()) if len(wf_valid) else np.nan
    LOG('   Walk-Forward 为正窗口比例 %.3f（%d/%d，阈值 >%.2f）'
        % (wf_pos, int((wf_valid['ic'] > 0).sum()), len(wf_valid), PREREG['wf_pos_frac']))

    # ══════════════════════════════════════════════ §41 / §42（基于 WF 分数）
    LOG.sep()
    LOG('§41 分年 / §42 Regime（统一使用 Walk-Forward 分数 = 严格样本外）')
    # 用 WF 分数重建一片面板（每行按其年份的训练窗打分）
    sc_wf = np.full(len(TD), np.nan)
    for ty in PREREG['year_report']:
        tr = sample & (year >= ty - 3) & (year <= ty - 1)
        if tr.sum() < 1000:
            continue
        feats = MODELS[PRIMARY_MODEL]
        b, _ = _ols(X[:, [allf.index(f) for f in feats]], yv, tr, feats)
        sc_wf[year == ty] = X[year == ty][:, [allf.index(f) for f in feats]] @ b
    SCW = to_panel(TD, sc_wf, NCODES, NCAL)
    VALW = np.isfinite(SCW) & np.isfinite(TG) & (EP == 1)
    RSw = _row_ranks(np.where(VALW, SCW, np.nan))
    RTw = _row_ranks(np.where(VALW, TG, np.nan))
    icw = _corr_rows(RSw, RTw, VALW)
    auw = _auc_top_rows(RSw, TG, VALW)
    rows_year = []
    for yy in PREREG['year_report']:
        my = np.zeros(NCAL, dtype=bool)
        my[day['year'] == yy] = True
        st = ic_stats(icw[my], name='y%d' % yy)
        sta = ic_stats(auw[my], name='y%d_auc' % yy)
        rows_year.append({'year': yy, 'n_day': st['n'], 'ic': st['mean'],
                          'ic_t': st['t_nw'], 'auc': sta['mean']})
        LOG('     %d  日=%4d  IC %+.4f (t %+.2f)  AUC %.4f'
            % (yy, st['n'], st['mean'], st['t_nw'], sta['mean']))
    YR = pd.DataFrame(rows_year)
    yv_ok = YR[np.isfinite(YR['ic'])]
    year_pos = float((yv_ok['ic'] > 0).mean()) if len(yv_ok) else np.nan
    LOG('   分年为正比例 %.3f（%d/%d，阈值 >=%.2f）'
        % (year_pos, int((yv_ok['ic'] > 0).sum()), len(yv_ok), PREREG['year_pos_frac']))

    reg_names = {0: 'BEAR', 1: 'NORMAL', 2: 'BULL'}
    rows_reg, reg_ic = [], {}
    for rv in (0, 1, 2):
        m = np.zeros(NCAL, dtype=bool)
        m[day_regime == rv] = True
        st = ic_stats(icw[m], name='reg%d' % rv)
        reg_ic[reg_names[rv]] = st['mean']
        rows_reg.append({'regime': reg_names[rv], 'n_day': st['n'],
                         'ic': st['mean'], 'ic_t': st['t_nw']})
        LOG('     %-7s 日=%4d  IC %+.4f (t %+.2f)' % (reg_names[rv], st['n'],
                                                      st['mean'], st['t_nw']))
    REGT = pd.DataFrame(rows_reg)
    seq = [reg_ic[k] for k in ('BEAR', 'NORMAL', 'BULL') if np.isfinite(reg_ic[k])]
    flips = int(sum(1 for i in range(len(seq) - 1)
                    if np.sign(seq[i]) != np.sign(seq[i + 1]))) if len(seq) > 1 else 0
    LOG('   Regime 方向翻转 %d 次（阈值 <=%d）' % (flips, PREREG['regime_flip_max']))

    # ══════════════════════════════════════════════ §43 参数邻域敏感性
    LOG.sep()
    LOG('§43 参数邻域敏感性（PREREG.grid_*；一次只扰动一个参数，其余保持预注册值；全报）')
    rows_grid = []
    dur0 = int(PREREG['pb_min_days'])
    dep0 = float(PREREG['pb_min_depth'])

    def grid_e1(dep, dur, hh=H_MAIN):
        sel = (in_ep == 1) & (TD['depth'].values >= dep) & (TD['ep_days'].values >= dur)
        sub = TD.loc[sel, ['code', 'leg', 'k', 'date', 'year', ycol]].copy()
        sub = sub.sort_values('k').drop_duplicates(['code', 'leg'], keep='first')
        v = sub[ycol].values.astype(np.float64)
        v = v[np.isfinite(v)]
        dm = sub.loc[np.isfinite(sub[ycol].values)].groupby('date')[ycol].mean()
        st = ic_stats(dm.values, name='grid')
        return {'n_event': int(len(v)), 'n_day': int(len(dm)),
                'mean': float(v.mean()) if len(v) else np.nan,
                'median': float(np.median(v)) if len(v) else np.nan,
                'winrate': float((v > 0).mean()) if len(v) else np.nan,
                't_nw': st['t_nw']}

    for dep in (dep0,) + tuple(PREREG['grid_depth']):
        r = grid_e1(dep, dur0)
        r.update({'param': 'pb_min_depth', 'value': dep, 'status': 'OK',
                  'note': '预注册基准' if dep == dep0 else '一次扰动'})
        rows_grid.append(r)
    for dur in (dur0,) + tuple(PREREG['grid_duration']):
        r = grid_e1(dep0, dur)
        r.update({'param': 'pb_min_days', 'value': dur, 'status': 'OK',
                  'note': '预注册基准' if dur == dur0 else '一次扰动'})
        rows_grid.append(r)
    for pf in PREREG['grid_mom_fast']:
        rows_grid.append({'param': 'ma_fast', 'value': pf, 'status': 'NOT_RUN_NEEDS_RERUN',
                          'n_event': np.nan, 'n_day': np.nan, 'mean': np.nan,
                          'median': np.nan, 'winrate': np.nan, 't_nw': np.nan,
                          'note': '需重跑 hp_panel + hp_events（MA 窗口改变趋势定义），本脚本不重复实现'})
    for ps in PREREG['grid_mom_slow']:
        rows_grid.append({'param': 'ma_slow', 'value': ps, 'status': 'NOT_RUN_NEEDS_RERUN',
                          'n_event': np.nan, 'n_day': np.nan, 'mean': np.nan,
                          'median': np.nan, 'winrate': np.nan, 't_nw': np.nan,
                          'note': '需重跑 hp_panel + hp_events（MA 窗口改变趋势定义），本脚本不重复实现'})
    GRID = pd.DataFrame(rows_grid)
    for _, r in GRID[GRID['status'] == 'OK'].iterrows():
        LOG('     %-13s = %-5s  n=%6d  mean %+.4f  median %+.4f  win %.3f  t %+.2f  [%s]'
            % (r['param'], r['value'], r['n_event'], r['mean'], r['median'],
               r['winrate'], r['t_nw'], r['note']))
    for _, r in GRID[GRID['status'] != 'OK'].iterrows():
        LOG('     %-13s = %-5s  ** %s **（如实标注，不静默）'
            % (r['param'], r['value'], r['status']))

    # ══════════════════════════════════════════════ Gate 判定（P4 可判部分）
    LOG.sep()
    LOG('Gate 判定（P4 覆盖 G8/G9/G10/G12；其余 Gate 属 P3/P5/P6，此处不越权判定）')
    o_v = oos_eval[PRIMARY_MODEL]['OOS']
    ic_o, auc_o, pr_o = o_v[0]['mean'], o_v[1]['mean'], o_v[2]['mean']
    mono_o = o_v[3]
    g8 = 'PASS' if (np.isfinite(wf_pos) and wf_pos > PREREG['wf_pos_frac']) else 'FAIL'
    g9 = 'PASS' if (np.isfinite(year_pos) and year_pos >= PREREG['year_pos_frac']) else 'FAIL'
    g10 = 'PASS' if flips <= PREREG['regime_flip_max'] else 'FAIL'
    g12_ic = np.isfinite(ic_o) and abs(ic_o) >= PREREG['ic_min']
    g12_au = np.isfinite(auc_o) and auc_o >= PREREG['auc_min']
    g12_pr = np.isfinite(pr_o) and pr_o >= PREREG['prec10_min']
    # PREREG['monotonic_required']=True 未附数值阈值；此处取「十分位均值序列与桶序号的秩相关
    # 与 OOS IC 同号」这一最弱可辩护口径，并全报 mono_rho 供复核，不另设阈值。
    g12_mo = ((not PREREG['monotonic_required'])
              or (np.isfinite(mono_o) and np.isfinite(ic_o)
                  and np.sign(mono_o) == np.sign(ic_o) and mono_o != 0.0))
    g12 = 'PASS' if (g12_ic and g12_au and g12_pr and g12_mo) else 'FAIL'
    rows_gate = [
        {'gate': 'G8', 'desc': 'Walk-Forward 多数窗口为正（阈值 >%.2f）' % PREREG['wf_pos_frac'],
         'value': wf_pos, 'verdict': g8},
        {'gate': 'G9', 'desc': '跨年份为正比例 >=%.2f' % PREREG['year_pos_frac'],
         'value': year_pos, 'verdict': g9},
        {'gate': 'G10', 'desc': 'Regime 方向翻转 <=%d' % PREREG['regime_flip_max'],
         'value': flips, 'verdict': g10},
        {'gate': 'G12', 'desc': 'OOS |IC|>=%.2f & AUC>=%.2f & Prec10>=%.2f & 单调'
         % (PREREG['ic_min'], PREREG['auc_min'], PREREG['prec10_min']),
         'value': ic_o, 'verdict': g12},
    ]
    for r in rows_gate:
        LOG('   %-5s %-52s value=%s -> %s'
            % (r['gate'], r['desc'], ('%+.4f' % r['value']) if isinstance(r['value'], float)
               else r['value'], r['verdict']))
    LOG('   （OOS 明细：IC %+.4f / AUC %.4f / Prec10 %.4f / mono %+.3f）'
        % (ic_o, auc_o, pr_o, mono_o))
    LOG('   致命 Gate 属本层者为 G8；G8=%s → %s'
        % (g8, '未触发致命拒绝，可继续 P5' if g8 == 'PASS' else
           '触发致命 Gate，禁止进入策略层（§52），P5/P6 仅作记录不改变结论'))
    LOG('   TRADING_AUTHORIZATION = %s' % PREREG['trading_authorization'])

    # ══════════════════════════════════════════════ 落盘
    EVAL.to_csv(os.path.join(OUTD, 'hp_model_eval.csv'), index=False)
    COEF.to_csv(os.path.join(OUTD, 'hp_model_coef.csv'), index=False)
    WF.to_csv(os.path.join(OUTD, 'hp_model_wf.csv'), index=False)
    YR.to_csv(os.path.join(OUTD, 'hp_model_year.csv'), index=False)
    REGT.to_csv(os.path.join(OUTD, 'hp_model_regime.csv'), index=False)
    GRID.to_csv(os.path.join(OUTD, 'hp_model_grid.csv'), index=False)
    pd.DataFrame(rows_gate).to_csv(os.path.join(OUTD, 'hp_model_gates.csv'), index=False)
    LOG.sep()
    LOG('落盘: out/hp_model_eval.csv / hp_model_coef.csv / hp_model_wf.csv / '
        'hp_model_year.csv / hp_model_regime.csv / hp_model_grid.csv / hp_model_gates.csv')
    LOG('P4 完成 %.0fs' % (time.time() - t0))
    LOG.sep('=')
    print('DONE')
    return 0


if __name__ == '__main__':
    sys.exit(main())
