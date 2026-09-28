# -*- coding: utf-8 -*-
"""
H-IND-DIFFUSION-01  P4：基准统计（STEP 10-12 / §21-§32 / §44）

隔离声明：本模块自包含，不 import 任何既有研究模块（TL-01 / H-TREND-PULLBACK / HVT / ARCHIVE）。

产出（§57 至少项；model_ladder / baseline_results 为 G4/G5/G20 判据的唯一来源）
    ic_results.csv          Rank IC + Pearson IC（mean / ICIR / NW t / 正比例）
    auc_results.csv         AUC / AUC-0.5 / Prec@10 / Recall@10
    quantile_results.csv    分位收益 Q1..QN + Top-Bottom + 单调性（ALL 与行业内部两种口径，§28）
    fama_macbeth.csv        FM beta / NW t（含市场层时序控制，§24）
    model_ladder.csv        M0-M3 模型阶梯（§22；G4/G5 判据来源）
    baseline_results.csv    B1-B4 + DIFF 组合 + 增量 alpha（§43/§44；G20 判据来源）
    data/panel/_meta_p4.json
    data/panel/_strat_p4.npz   组合日收益序列 + 每日持仓掩码（供 P6 成本/换手/尾部复用）

预声明判定口径（**在看到任何数值之前冻结**；P7 只按此口径读表，不得改口径救援 §54）
    G4 = h=5 & phase=ALL：M2 的 ind_breadth 系数 beta>0 且 t_NW>0，且 OOS 同号
    G5 = h=5 & phase=ALL：M3 的 diffusion   系数 beta>0 且 t_NW>0，且 OOS 同号
    G20= h=5 & phase=ALL：(DIFF - B4) 日均增量收益>0 且 t_NW>0（NW lag=h），且 OOS 同号
    组合候选集统一为 CAND（§44 可比性）：valid & 行业 eligible & ret_20 与 exposure 均可得。
    所有策略等权、行业 Top3、行业内 Top20%、k 日信息选股、k+1 开盘进场（§43）。

口径说明
    - 行业归属取 P1 的逐日 PIT 面板 _code_ind_l1（2D，含 in_date/out_date 切换）。
    - 暴露来自 P3；行业层变量来自 P2（经 P3 映射到个股）。
    - §24 市场层变量无横截面变异（会被截距吸收），故在第二段时序上做 FWL 残差化后取 NW t。
    - 分位排序的「行业内部」口径只对行业内可变信号有意义（行业层信号在行业内为常数）。
"""
import os
import time
import warnings

import numpy as np
import pandas as pd

# 逐日横截面 nanmedian / nanmean 在空列上会产生大量 RuntimeWarning（属预期：
# 部分日期某行业无有效成员），过滤以保持日志可读；不影响任何数值。
warnings.filterwarnings('ignore', category=RuntimeWarning,
                        message=r'.*(All-NaN|empty slice|Degrees of freedom).*')

from hid_common import (DATA, PREREG, mklog, pload, pload_day, pload_meta,
                        code_grid, rows_by_code, ic_stats, daily_ic, auc_binary,
                        rank_avg, ols_beta_se, monotonicity, _pearson, out_csv,
                        psave_meta_side, PHASES, prereg_hash, derived_hash)

LOG = mklog('baseline')
PAN = os.path.join(DATA, 'panel')
HERE = os.path.dirname(os.path.abspath(__file__))

HZ = tuple(PREREG['primary_horizons'])        # (3, 5, 10)
NQ = int(PREREG['n_quantiles'])               # 5
NQ_DEC = 10                                   # §29 十分位检查
MIN_XS = 30                                   # 日横截面最小样本
MIN_IND = int(PREREG['ind_n_min'])            # 20
TOPK_IND = 3                                  # §43 行业 Top3
TOP_FRAC = 0.20                               # §43 行业内 Top20%
PH_NAMES = ['ALL'] + list(PHASES)
REG_NAMES = ['BEAR', 'NORMAL', 'BULL']        # P1 regime 编码 0/1/2

# 单变量信号：(名称, 面板列, 类别)；STOCK = 行业内可变，IND = 行业层（行业内为常数）
SIGS = [
    ('exposure',            'expo_expo_ind',            'STOCK'),
    ('expo_ind',            'expo_expo_ind_only',       'STOCK'),
    ('expo_rs',             'expo_expo_rs_only',        'STOCK'),
    ('rs_20',               'expo_rs_20',               'STOCK'),
    ('ret_20',              'ret_20',                   'STOCK'),
    ('ret_5',               'ret_5',                    'STOCK'),
    ('ind_diffusion_score', 'expo_ind_diffusion_score', 'IND'),
    ('ind_pct_dchg_5',      'expo_ind_pct_dchg_5',      'IND'),
    ('ind_pct_accel20',     'expo_ind_pct_accel20',     'IND'),
    ('ind_pct_lb_020',      'expo_ind_pct_lb_020',      'IND'),
    ('ind_pct_rsb_chg_5',   'expo_ind_pct_rsb_chg_5',   'IND'),
    ('ind_dchg_5',          'expo_ind_dchg_5',          'IND'),
    ('ind_accel20',         'expo_ind_accel20',         'IND'),
    ('ind_lb_020',          'expo_ind_lb_020',          'IND'),
    ('ind_rsb_chg_5',       'expo_ind_rsb_chg_5',       'IND'),
    ('ind_up_20',           'expo_ind_up_20',           'IND'),
    ('ind_diff_gap',        'expo_ind_diff_gap',        'IND'),
    ('ind_mom_20',          'expo_ind_mom_20',          'IND'),
    ('ind_n_used',          'expo_ind_n_used',          'IND'),
]
SRC = {s[0]: s[1] for s in SIGS}
KIND = {s[0]: s[2] for s in SIGS}

AUC_SIGS = ['exposure', 'expo_ind', 'expo_rs', 'rs_20', 'ind_diffusion_score',
            'ret_20', 'ret_5', 'ind_mom_20', 'ind_dchg_5', 'ind_up_20', 'ind_lb_020']
CORE_SIGS = ['exposure', 'expo_ind', 'expo_rs', 'rs_20', 'ret_20', 'ind_diffusion_score']

# §22 模型阶梯 / §32 Fama-MacBeth：预注册符号 -> 面板列
XLADDER = {'stock_mom': 'ret_20', 'ind_mom': 'expo_ind_mom_20',
           'ind_breadth': 'expo_ind_up_20', 'diffusion': 'expo_expo_ind'}
XFM = {'diffusion': 'expo_expo_ind', 'stock_mom': 'ret_20', 'ind_mom': 'expo_ind_mom_20',
       'size': 'ln_mv', 'liquidity': 'ln_amt', 'volatility': 'rv_20'}
XALL = dict(XLADDER)
XALL.update(XFM)
MKT_CTRL = ['mkt_ret_20', 'mkt_breadth_20']   # §24


# ══════════════════════════════════════════════════════════════ 工具

def f32(name):
    return np.asarray(pload(name), dtype=np.float32)


def daily_pearson(sig, tgt, VAL, min_xs=MIN_XS):
    """逐日横截面 Pearson IC。返回 (NCAL,)"""
    NCAL = sig.shape[1]
    out = np.full(NCAL, np.nan)
    for k in range(NCAL):
        s = np.asarray(sig[VAL[:, k], k], dtype=np.float64)
        t = np.asarray(tgt[VAL[:, k], k], dtype=np.float64)
        f = np.isfinite(s) & np.isfinite(t)
        if int(f.sum()) < min_xs:
            continue
        out[k] = _pearson(s[f], t[f])
    return out


def daily_prec_rec(sig, lab, VAL, frac=0.10, min_n=MIN_XS):
    """逐日 Precision@top-frac / Recall@top-frac（信号降序）"""
    NCAL = sig.shape[1]
    prec = np.full(NCAL, np.nan)
    rec = np.full(NCAL, np.nan)
    for k in range(NCAL):
        s = np.asarray(sig[VAL[:, k], k], dtype=np.float64)
        y = np.asarray(lab[VAL[:, k], k], dtype=np.float64)
        f = np.isfinite(s) & np.isfinite(y)
        n = int(f.sum())
        if n < min_n:
            continue
        ss, yy = s[f], y[f]
        ntop = max(1, int(round(frac * n)))
        idx = np.argsort(-ss, kind='stable')[:ntop]
        prec[k] = float(yy[idx].mean())
        tot = float(yy.sum())
        rec[k] = float(yy[idx].sum() / tot) if tot > 0 else np.nan
    return prec, rec


def ind_median_map(x, VAL, ind2, RB, min_n=5):
    """逐 (日, 行业) 中位数映射回个股面板；返回 (NCODES, NCAL)"""
    out = np.full(x.shape, np.nan, dtype=np.float64)
    for c, rows in RB.items():
        sub = (ind2[rows, :] == c) & VAL[rows, :] & np.isfinite(x[rows, :])
        n = sub.sum(0)
        with np.errstate(invalid='ignore'):
            med = np.nanmedian(np.where(sub, x[rows, :], np.nan), axis=0)
        keep = np.isfinite(med) & (n >= min_n)
        out[rows, :] = np.where(sub & keep[None, :], med[None, :], np.nan)
    return out


def quantile_scan(sig, tgt, VAL, ind2, RB, nqs, scopes):
    """分位扫描（ALL / WITHIN_IND 一次遍历完成）。返回 {(scope, nq): (S, C)}"""
    NCAL = sig.shape[1]
    Su, Cn = {}, {}
    for sc in scopes:
        for nq in nqs:
            Su[(sc, nq)] = np.zeros((NCAL, nq))
            Cn[(sc, nq)] = np.zeros((NCAL, nq), dtype=np.int64)
    for k in range(NCAL):
        m = VAL[:, k] & np.isfinite(sig[:, k]) & np.isfinite(tgt[:, k])
        if int(m.sum()) < MIN_XS:
            continue
        idx = np.nonzero(m)[0]
        s = np.asarray(sig[idx, k], dtype=np.float64)
        t = np.asarray(tgt[idx, k], dtype=np.float64)
        n = len(s)
        if 'ALL' in scopes:
            r = rank_avg(s)
            for nq in nqs:
                b = np.minimum(nq - 1, ((r - 1.0) / n * nq).astype(np.int64))
                for q in range(nq):
                    sel = (b == q)
                    if sel.any():
                        Su[('ALL', nq)][k, q] = t[sel].sum()
                        Cn[('ALL', nq)][k, q] = int(sel.sum())
        if 'WITHIN_IND' in scopes:
            icode = ind2[idx, k]
            for c, _rows in RB.items():
                pos = np.nonzero(icode == c)[0]
                if len(pos) < MIN_IND:
                    continue
                rr = rank_avg(s[pos])
                nn = len(pos)
                for nq in nqs:
                    b = np.minimum(nq - 1, ((rr - 1.0) / nn * nq).astype(np.int64))
                    for q in range(nq):
                        sel = (b == q)
                        if sel.any():
                            Su[('WITHIN_IND', nq)][k, q] += t[pos][sel].sum()
                            Cn[('WITHIN_IND', nq)][k, q] += int(sel.sum())
    res = {}
    for kk in Su:
        S = np.where(Cn[kk] > 0, Su[kk] / np.maximum(Cn[kk], 1), np.nan)
        res[kk] = (S, Cn[kk])
    return res


def _std_cols(M):
    """按列标准化（行已保证全有限）；SD 退化返回 None"""
    Z = np.empty_like(M, dtype=np.float64)
    for j in range(M.shape[1]):
        v = M[:, j]
        sd = float(v.std(ddof=1))
        if not (sd > 1e-12):
            return None
        Z[:, j] = (v - v.mean()) / sd
    return Z


def reg_one(y, Xs):
    """单次横截面 OLS（自变量按列标准化）。返回 (betas, r2, n)"""
    yv = np.asarray(y, dtype=np.float64)
    M = np.column_stack([np.asarray(x, dtype=np.float64) for x in Xs])
    Z = _std_cols(M)
    if Z is None:
        return None
    b, _ = ols_beta_se(Z, yv)
    if not np.isfinite(b).all():
        return None
    A = np.column_stack([np.ones(len(yv)), Z])
    e = yv - A @ b
    sst = float(((yv - yv.mean()) ** 2).sum())
    r2 = 1.0 - float((e * e).sum()) / sst if sst > 0 else np.nan
    return b, r2, len(yv)


def run_reg(ARR, SCOPE, RB, ycol, xnames, sample, min_n=MIN_IND):
    """模型阶梯 / FM 的逐日横截面回归。

    sample='ALL'        -> 全市场日内横截面
    sample='WITHIN_IND' -> 逐 (日, 行业) 回归后按日等权平均（§23 行业内部口径）
    返回 (B: {term: (NCAL,)}, R2: (NCAL,), N: (NCAL,) 回归个数)
    """
    NCAL = ARR[ycol].shape[1]
    cols = [ARR[XALL[n]] for n in xnames]
    y = ARR[ycol]
    B = {n: np.full(NCAL, np.nan) for n in ['const'] + list(xnames)}
    R2 = np.full(NCAL, np.nan)
    N = np.zeros(NCAL, dtype=np.int64)
    if sample == 'ALL':
        for k in range(NCAL):
            m = SCOPE[:, k].copy() & np.isfinite(y[:, k])
            for c in cols:
                m &= np.isfinite(c[:, k])
            if int(m.sum()) < min_n:
                continue
            r = reg_one(y[m, k], [c[m, k] for c in cols])
            if r is None:
                continue
            b, r2, n = r
            B['const'][k] = b[0]
            for i, nm in enumerate(xnames):
                B[nm][k] = b[i + 1]
            R2[k] = r2
            N[k] = n
    else:
        for k in range(NCAL):
            base = SCOPE[:, k] & np.isfinite(y[:, k])
            acc = {n: [] for n in ['const'] + list(xnames)}
            r2s = []
            for _c, rows in RB.items():
                m = base[rows].copy()
                if not m.any():
                    continue
                for cc in cols:
                    m &= np.isfinite(cc[rows, k])
                if int(m.sum()) < min_n:
                    continue
                r = reg_one(y[rows[m], k], [cc[rows[m], k] for cc in cols])
                if r is None:
                    continue
                b, r2, _n = r
                acc['const'].append(b[0])
                for i, nm in enumerate(xnames):
                    acc[nm].append(b[i + 1])
                r2s.append(r2)
            if acc['const']:
                B['const'][k] = float(np.mean(acc['const']))
                for nm in xnames:
                    B[nm][k] = float(np.mean(acc[nm]))
                R2[k] = float(np.mean(r2s))
                N[k] = len(acc['const'])
    return B, R2, N


def ts_control(series, ctrls, m, lag=19):
    """§24 第二段时序控制：日度 beta 序列对市场层变量 FWL 残差化。

    FWL：含截距回归的截距 = 残差均值。返回 (resid_mean, t_nw, n)
    """
    y = np.asarray(series, dtype=np.float64)
    ok = np.asarray(m, dtype=bool) & np.isfinite(y)
    for c in ctrls:
        ok &= np.isfinite(c)
    if int(ok.sum()) < 30:
        return np.nan, np.nan, int(ok.sum())
    yv = y[ok]
    X = np.column_stack([np.asarray(c, dtype=np.float64)[ok] for c in ctrls])
    A = np.column_stack([np.ones(len(yv)), X])
    b, _ = ols_beta_se(X, yv)                 # 函数内自带截距
    resid = yv - A @ b
    st = ic_stats(resid, lag=lag)
    return st['mean'], st['t_nw'], st['n']


def ind_top(g_ok, g_val, topk):
    """逐日按行业层指标降序取 Top-K 行业代码（行 0 = UNKNOWN 自然被排除）"""
    n_ind, NCAL = g_val.shape
    out = np.full((topk, NCAL), -1, dtype=np.int16)
    for k in range(NCAL):
        ok = (np.isfinite(g_ok[:, k]) & (g_ok[:, k] > 0.5)
              & np.isfinite(g_val[:, k]))
        ok[0] = False
        if not ok.any():
            continue
        idx = np.nonzero(ok)[0]
        order = idx[np.argsort(-g_val[idx, k], kind='stable')]
        ntop = min(topk, len(order))
        out[:ntop, k] = order[:ntop]
    return out


def member(top, ind2, k):
    codes = top[:, k]
    codes = codes[codes > 0]
    if len(codes) == 0:
        return np.zeros(ind2.shape[0], dtype=bool)
    return np.isin(ind2[:, k], codes)


def top_frac(sig_col, base, frac):
    m = np.zeros(len(sig_col), dtype=bool)
    idx = np.nonzero(np.asarray(base, dtype=bool) & np.isfinite(sig_col))[0]
    if len(idx) == 0:
        return m
    ntop = max(1, int(np.ceil(frac * len(idx))))
    m[idx[np.argsort(-sig_col[idx], kind='stable')[:ntop]]] = True
    return m


# ══════════════════════════════════════════════════════════════ 主流程

def main():
    t_start = time.time()
    LOG.sep('=')
    LOG('H-IND-DIFFUSION-01  P4 基准统计（STEP10-12）  hash=%s derived=%s  %s'
        % (prereg_hash(), derived_hash(), time.strftime('%Y-%m-%d %H:%M:%S')))
    LOG.sep()

    meta = pload_meta()
    NCODES, NCAL = int(meta['NCODES']), int(meta['NCAL'])
    n_l1 = int(meta['n_l1'])
    day = pload_day()
    phc = np.asarray(day['phase']).astype(np.int8)
    regc = np.asarray(day['regime']).astype(np.int8)
    PHM = {'ALL': np.ones(NCAL, dtype=bool)}
    for i, nm in enumerate(PHASES):
        PHM[nm] = (phc == i + 1)
    REGM = {nm: (regc == i) for i, nm in enumerate(REG_NAMES)}
    LOG('  面板 %d 股 × %d 日；分期 %s'
        % (NCODES, NCAL, {k: int(v.sum()) for k, v in PHM.items() if k != 'ALL'}))
    LOG('  Regime %s' % {k: int(v.sum()) for k, v in REGM.items()})

    ind_l1 = np.asarray(pload('_code_ind_l1'))
    SCOPE = (np.asarray(pload('expo_expo_scope')) == 1)
    n_valid = int((np.asarray(pload('valid')) == 1).sum())
    LOG('  暴露范围 SCOPE = %d 股票日（占 valid %.4f）'
        % (int(SCOPE.sum()), SCOPE.sum() / max(1, n_valid)))

    RB = rows_by_code(ind_l1, NCODES)
    LOG('  行业分组：%d 个 L1 行业' % len(RB))
    EXE = {h: np.asarray(pload('exe_%d' % h), dtype=np.float32) for h in HZ}

    # ══════════════════════════════════════ STEP10-A  IC（Rank + Pearson）
    LOG.sep('─')
    LOG('[STEP10-A] 单变量 IC（Rank / Pearson）× horizon × phase')
    t0 = time.time()
    ic_rows = []
    for name, src, kind in SIGS:
        sig = f32(src)
        for h in HZ:
            rs = daily_ic(sig, EXE[h], SCOPE, min_xs=MIN_XS)
            ps = daily_pearson(sig, EXE[h], SCOPE, min_xs=MIN_XS)
            for ph in PH_NAMES:
                st = ic_stats(rs[PHM[ph]], lag=19)
                sp = ic_stats(ps[PHM[ph]], lag=19)
                ic_rows.append({
                    'signal': name, 'kind': kind, 'horizon': h, 'phase': ph,
                    'n_days': st['n'], 'mean_ic': st['mean'], 'std_ic': st['std'],
                    'icir': st['icir'], 't_nw': st['t_nw'], 'pos_ratio': st['pos_ratio'],
                    'mean_ic_pearson': sp['mean'], 't_nw_pearson': sp['t_nw']})
        del sig
    out_csv('ic_results.csv', pd.DataFrame(ic_rows))
    LOG('  [SAVE] ic_results.csv rows=%d（%.1fs）' % (len(ic_rows), time.time() - t0))
    for name in ('exposure', 'expo_rs', 'ind_diffusion_score', 'ind_mom_20', 'ret_20'):
        for h in HZ:
            r = [x for x in ic_rows if x['signal'] == name and x['horizon'] == h
                 and x['phase'] == 'ALL'][0]
            LOG('    %-20s h=%-2d IC=%+.4f t_NW=%+.2f pos=%.2f'
                % (name, h, r['mean_ic'], r['t_nw'], r['pos_ratio']))

    # ══════════════════════════════════════ STEP10-B  AUC / Prec@10 / Recall@10
    LOG.sep('─')
    LOG('[STEP10-B] AUC / Prec@10 / Recall@10（§30）')
    t0 = time.time()
    auc_rows = []
    MED = {h: ind_median_map(np.asarray(EXE[h], dtype=np.float64), SCOPE, ind_l1, RB)
           for h in HZ}
    for name in AUC_SIGS:
        sig = f32(SRC[name])
        for h in HZ:
            eh = np.asarray(EXE[h], dtype=np.float64)
            with np.errstate(invalid='ignore'):
                labs = {
                    'gt0': np.where(np.isfinite(eh), (eh > 0).astype(np.float64), np.nan),
                    'gt_ind_median': np.where(np.isfinite(eh) & np.isfinite(MED[h]),
                                              (eh > MED[h]).astype(np.float64), np.nan)}
            f_all = SCOPE & np.isfinite(labs['gt0'])
            pos_rate_all = float(labs['gt0'][f_all].mean()) if f_all.any() else np.nan
            for lab_name, lab in labs.items():
                a = auc_binary(sig, lab, SCOPE, min_n=MIN_XS)
                pr, rc = daily_prec_rec(sig, lab, SCOPE, frac=0.10)
                for ph in PH_NAMES:
                    m = PHM[ph]
                    st = ic_stats(a[m], lag=19)
                    sd = ic_stats(a[m] - 0.5, lag=19)   # 对 AUC 偏离 0.5 做检验；
                    # 直接对 AUC 水平做 t 检验是非信息性的（均值远离 0，t 恒为正大数）
                    auc_rows.append({
                        'signal': name, 'kind': KIND[name], 'label': lab_name, 'horizon': h,
                        'phase': ph, 'n_days': st['n'], 'auc': st['mean'],
                        'auc_minus_05': (st['mean'] - 0.5) if np.isfinite(st['mean']) else np.nan,
                        't_nw_vs_50': sd['t_nw'],
                        'prec10': float(np.nanmean(pr[m])) if m.any() else np.nan,
                        'recall10': float(np.nanmean(rc[m])) if m.any() else np.nan,
                        'pos_rate_all': pos_rate_all})
        del sig
    out_csv('auc_results.csv', pd.DataFrame(auc_rows))
    LOG('  [SAVE] auc_results.csv rows=%d（%.1fs）' % (len(auc_rows), time.time() - t0))
    for h in HZ:
        r = [x for x in auc_rows if x['signal'] == 'exposure' and x['horizon'] == h
             and x['label'] == 'gt_ind_median' and x['phase'] == 'ALL'][0]
        LOG('    exposure gt_ind_median h=%-2d AUC=%.4f Prec@10=%.4f Rec@10=%.4f'
            % (h, r['auc'], r['prec10'], r['recall10']))

    # ══════════════════════════════════════ STEP10-C  分位（§28 / §29）
    LOG.sep('─')
    LOG('[STEP10-C] 分位组合（ALL / WITHIN_IND；nq=5 全信号，nq=10 核心集）')
    t0 = time.time()
    q_rows = []

    def _emit_q(name, kind, scope, nq, h, S, C):
        for ph in PH_NAMES:
            m = PHM[ph] & np.isfinite(S).any(1)
            if not m.any():
                continue
            dm = np.nanmean(S[m], axis=0)
            dn = np.nansum(C[m], axis=0)
            row = {'signal': name, 'kind': kind, 'sort_scope': scope, 'nq': nq,
                   'horizon': h, 'phase': ph, 'n_days': int(m.sum())}
            for q in range(NQ_DEC):
                row['q%d' % (q + 1)] = float(dm[q]) if q < nq else np.nan
                row['n%d' % (q + 1)] = int(dn[q]) if q < nq else 0
            row['spread_top_bot'] = float(dm[nq - 1] - dm[0])
            rho, slope = monotonicity(dm[:nq])
            row['mono_rho'] = rho
            row['mono_slope'] = slope
            q_rows.append(row)

    for name, src, kind in SIGS:
        sig = f32(src)
        scopes = ['ALL', 'WITHIN_IND'] if kind == 'STOCK' else ['ALL']
        nqs = [NQ] + ([NQ_DEC] if name in CORE_SIGS else [])
        for h in HZ:
            res = quantile_scan(sig, EXE[h], SCOPE, ind_l1, RB, nqs, scopes)
            for (sc, nq), (S, C) in res.items():
                _emit_q(name, kind, sc, nq, h, S, C)
        del sig
    out_csv('quantile_results.csv', pd.DataFrame(q_rows))
    LOG('  [SAVE] quantile_results.csv rows=%d（%.1fs）' % (len(q_rows), time.time() - t0))
    for name in ('exposure', 'ind_mom_20'):
        for h in HZ:
            r = [x for x in q_rows if x['signal'] == name and x['sort_scope'] == 'ALL'
                 and x['nq'] == NQ and x['horizon'] == h and x['phase'] == 'ALL'][0]
            LOG('    %-16s h=%-2d Q1..Q5=[%s] Q5-Q1=%+.4f mono=%.3f'
                % (name, h, ' '.join('%.4f' % r['q%d' % i] for i in range(1, 6)),
                   r['spread_top_bot'], r['mono_rho']))

    # ══════════════════════════════════════ STEP11/12  模型阶梯 + Fama-MacBeth
    LOG.sep('─')
    LOG('[STEP11-12] M0-M3 阶梯（§22）+ Fama-MacBeth（§32）+ 市场层控制（§24）')
    t0 = time.time()
    ARR = {'ret_20': f32('ret_20'), 'ret_5': f32('ret_5'),
           'expo_expo_ind': f32('expo_expo_ind'),
           'expo_expo_ind_only': f32('expo_expo_ind_only'),
           'expo_ind_mom_20': f32('expo_ind_mom_20'),
           'expo_ind_up_20': f32('expo_ind_up_20'),
           'rv_20': f32('rv_20'),
           'ln_mv': np.log(np.maximum(f32('total_mv'), 1e-6)).astype(np.float32),
           'ln_amt': np.log(np.maximum(f32('amt_ma20'), 1e-6)).astype(np.float32)}
    # 回归因变量：前向可执行收益（主口径，见 PREREG['target_main']）
    for h in HZ:
        ARR['exe_%d' % h] = EXE[h]
    mkt = np.load(os.path.join(PAN, '_market.npz'))
    CTRL = [np.asarray(mkt[c], dtype=np.float64) for c in MKT_CTRL]

    ladder_rows = []
    fm_rows = []
    LADDER = PREREG['model_ladder']
    for sample in ('ALL', 'WITHIN_IND'):
        for model, terms in LADDER.items():
            for h in HZ:
                B, R2, N = run_reg(ARR, SCOPE, RB, 'exe_%d' % h, list(terms), sample)
                for ph in PH_NAMES:
                    m = PHM[ph] & (N > 0)
                    if not m.any():
                        continue
                    for nm in ['const'] + list(terms):
                        st = ic_stats(B[nm][m], lag=19)
                        ladder_rows.append({'sample': sample, 'model': model, 'horizon': h,
                                            'phase': ph, 'term': nm, 'beta': st['mean'],
                                            't_nw': st['t_nw'], 'n_day': st['n'],
                                            'r2_mean': float(np.nanmean(R2[m]))})
    out_csv('model_ladder.csv', pd.DataFrame(ladder_rows))
    LOG('  [SAVE] model_ladder.csv rows=%d' % len(ladder_rows))

    xfm = ['diffusion', 'stock_mom', 'ind_mom', 'size', 'liquidity', 'volatility']
    for sample in ('ALL', 'WITHIN_IND'):
        for h in HZ:
            B, R2, N = run_reg(ARR, SCOPE, RB, 'exe_%d' % h, xfm, sample)
            for ph in PH_NAMES:
                m = PHM[ph] & (N > 0)
                if not m.any():
                    continue
                for nm in xfm:
                    st = ic_stats(B[nm][m], lag=19)
                    fm_rows.append({'sample': sample, 'horizon': h, 'phase': ph, 'term': nm,
                                    'beta': st['mean'], 't_nw': st['t_nw'], 'n_day': st['n'],
                                    'r2_mean': float(np.nanmean(R2[m]))})
                rm, tw, nn = ts_control(B['diffusion'], CTRL, m, lag=19)
                fm_rows.append({'sample': '%s|MKTCONTROL' % sample, 'horizon': h, 'phase': ph,
                                'term': 'diffusion_resid', 'beta': rm, 't_nw': tw,
                                'n_day': nn, 'r2_mean': np.nan})
    out_csv('fama_macbeth.csv', pd.DataFrame(fm_rows))
    LOG('  [SAVE] fama_macbeth.csv rows=%d（%.1fs）' % (len(fm_rows), time.time() - t0))
    for sample in ('ALL', 'WITHIN_IND'):
        for h in HZ:
            r = [x for x in ladder_rows if x['sample'] == sample and x['model'] == 'M3'
                 and x['horizon'] == h and x['term'] == 'diffusion' and x['phase'] == 'ALL']
            r2 = [x for x in ladder_rows if x['sample'] == sample and x['model'] == 'M2'
                  and x['horizon'] == h and x['term'] == 'ind_breadth' and x['phase'] == 'ALL']
            if r and r2:
                LOG('    [%s] h=%-2d M3.diffusion beta=%+.5f t=%+.2f | '
                    'M2.breadth beta=%+.5f t=%+.2f'
                    % (sample, h, r[0]['beta'], r[0]['t_nw'], r2[0]['beta'], r2[0]['t_nw']))

    # ══════════════════════════════════════ STEP12  B1-B4 + DIFF（§43/§44）
    LOG.sep('─')
    LOG('[STEP12] 组合基准 B1-B4 + DIFF（行业 Top%d × 行业内 Top%.0f%%）'
        % (TOPK_IND, TOP_FRAC * 100))
    t0 = time.time()
    st = pd.read_parquet(os.path.join(HERE, 'industry_daily_state.parquet'))
    ft = pd.read_parquet(os.path.join(HERE, 'industry_diffusion_features.parquet'))
    st = st[st['ind_level'] == 'L1']
    ft = ft[ft['ind_level'] == 'L1']
    g_el = code_grid(st, 'eligible', n_l1, NCAL)
    g_mom = code_grid(st, 'mom_ew_20', n_l1, NCAL)
    g_diff = code_grid(ft, 'diffusion_score', n_l1, NCAL)
    g_n = code_grid(st, 'n_used', n_l1, NCAL)
    g_elb = (g_el > 0.5)          # 转 bool：np.nan 视作非 eligible
    TOPM = ind_top(g_elb & (g_n >= MIN_IND), g_mom, TOPK_IND)
    TOPD = ind_top(g_elb, g_diff, TOPK_IND)

    CAND = SCOPE & np.isfinite(ARR['ret_20']) & np.isfinite(ARR['expo_expo_ind'])
    LOG('  候选集 CAND = %d 股票日（占 valid %.4f）'
        % (int(CAND.sum()), CAND.sum() / max(1, n_valid)))

    SEL = {nm: np.zeros((NCODES, NCAL), dtype=np.int8)
           for nm in ('B1', 'B2', 'B3', 'B4', 'DIFF')}
    r20 = ARR['ret_20']
    expo = ARR['expo_expo_ind']
    for k in range(NCAL):
        base = CAND[:, k]
        if not base.any():
            continue
        SEL['B1'][:, k] = base.astype(np.int8)
        m2 = base & member(TOPM, ind_l1, k)
        SEL['B2'][:, k] = m2.astype(np.int8)
        SEL['B3'][:, k] = top_frac(r20[:, k], base, TOP_FRAC).astype(np.int8)
        SEL['B4'][:, k] = (m2 & top_frac(r20[:, k], m2, TOP_FRAC)).astype(np.int8)
        md = base & member(TOPD, ind_l1, k)
        SEL['DIFF'][:, k] = (md & top_frac(expo[:, k], md, TOP_FRAC)).astype(np.int8)

    strat = {}
    base_rows = []
    for nm in ('B1', 'B2', 'B3', 'B4', 'DIFF'):
        hold = SEL[nm].sum(0).astype(np.float64)
        strat['hold_%s' % nm] = hold
        for h in HZ:
            r = np.full(NCAL, np.nan)
            for k in range(NCAL):
                m = SEL[nm][:, k] > 0
                if m.any():
                    v = np.asarray(EXE[h][m, k], dtype=np.float64)
                    v = v[np.isfinite(v)]
                    if len(v):
                        r[k] = float(v.mean())
            strat['%s_h%d' % (nm, h)] = r
            for ph in PH_NAMES:
                m = PHM[ph] & np.isfinite(r)
                stt = ic_stats(r[m], lag=h)
                base_rows.append({'strategy': nm, 'horizon': h, 'phase': ph,
                                  'n_days': stt['n'], 'mean_ret': stt['mean'],
                                  'ann_ret': (stt['mean'] * 243) if np.isfinite(stt['mean']) else np.nan,
                                  't_nw': stt['t_nw'], 'pos_ratio': stt['pos_ratio'],
                                  'avg_hold': float(np.nanmean(hold[m])) if m.any() else np.nan})
    for nm in ('B4', 'B2', 'B3'):
        for h in HZ:
            rr = strat['DIFF_h%d' % h] - strat['%s_h%d' % (nm, h)]
            strat['DIFF-%s_h%d' % (nm, h)] = rr
            for ph in PH_NAMES:
                m = PHM[ph] & np.isfinite(rr)
                stt = ic_stats(rr[m], lag=h)
                base_rows.append({'strategy': 'DIFF-%s' % nm, 'horizon': h, 'phase': ph,
                                  'n_days': stt['n'], 'mean_ret': stt['mean'],
                                  'ann_ret': (stt['mean'] * 243) if np.isfinite(stt['mean']) else np.nan,
                                  't_nw': stt['t_nw'], 'pos_ratio': stt['pos_ratio'],
                                  'avg_hold': np.nan})
    out_csv('baseline_results.csv', pd.DataFrame(base_rows))
    np.savez(os.path.join(PAN, '_strat_p4.npz'),
             **{kk: np.asarray(vv, dtype=np.float64) for kk, vv in strat.items()},
             **{'sel_%s' % nm: SEL[nm] for nm in SEL})
    LOG('  [SAVE] baseline_results.csv rows=%d；_strat_p4.npz（%.1fs）'
        % (len(base_rows), time.time() - t0))
    for nm in ('B1', 'B2', 'B3', 'B4', 'DIFF', 'DIFF-B4'):
        r = [x for x in base_rows if x['strategy'] == nm and x['horizon'] == 5
             and x['phase'] == 'ALL']
        if r:
            LOG('    %-8s h=5 ALL mean=%+.5f ann=%+.2f%% t_NW=%+.2f hold=%.1f'
                % (nm, r[0]['mean_ret'], 100 * r[0]['ann_ret'], r[0]['t_nw'], r[0]['avg_hold']))

    # ══════════════════════════════════════ 元信息 + 预声明判据取值
    def _lam(model, term, sample, h, ph):
        r = [x for x in ladder_rows if x['model'] == model and x['term'] == term
             and x['sample'] == sample and x['horizon'] == h and x['phase'] == ph]
        return (r[0]['beta'], r[0]['t_nw'], r[0]['n_day']) if r else (np.nan, np.nan, 0)

    def _bl(nm, h, ph):
        r = [x for x in base_rows if x['strategy'] == nm and x['horizon'] == h
             and x['phase'] == ph]
        return (r[0]['mean_ret'], r[0]['t_nw'], r[0]['n_days']) if r else (np.nan, np.nan, 0)

    g4_all = _lam('M2', 'ind_breadth', 'ALL', 5, 'ALL')
    g4_oos = _lam('M2', 'ind_breadth', 'ALL', 5, 'OOS')
    g5_all = _lam('M3', 'diffusion', 'ALL', 5, 'ALL')
    g5_oos = _lam('M3', 'diffusion', 'ALL', 5, 'OOS')
    g20_all = _bl('DIFF-B4', 5, 'ALL')
    g20_oos = _bl('DIFF-B4', 5, 'OOS')
    LOG.sep('=')
    LOG('[PRELIM] 预声明口径取值（G4/G5/G20；最终判定在 P7，需结合 Null/OOS/Cost/Tail）')
    LOG('  G4  M2.ind_breadth h5 ALL beta=%+.5f t=%+.2f | OOS beta=%+.5f t=%+.2f'
        % (g4_all[0], g4_all[1], g4_oos[0], g4_oos[1]))
    LOG('  G5  M3.diffusion   h5 ALL beta=%+.5f t=%+.2f | OOS beta=%+.5f t=%+.2f'
        % (g5_all[0], g5_all[1], g5_oos[0], g5_oos[1]))
    LOG('  G20 DIFF-B4        h5 ALL %+.5f t=%+.2f | OOS %+.5f t=%+.2f'
        % (g20_all[0], g20_all[1], g20_oos[0], g20_oos[1]))

    psave_meta_side('_meta_p4.json', {
        'p4_built_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'p4_prereg_hash': prereg_hash(), 'p4_derived_hash': derived_hash(),
        'p4_universe': {'n_scope': int(SCOPE.sum()), 'n_cand': int(CAND.sum()),
                        'n_codes': NCODES, 'n_cal': NCAL},
        'p4_declared_rule': {
            'G4': 'h=5 & phase=ALL: M2 的 ind_breadth beta>0 且 t_NW>0，且 OOS 同号',
            'G5': 'h=5 & phase=ALL: M3 的 diffusion beta>0 且 t_NW>0，且 OOS 同号',
            'G20': 'h=5 & phase=ALL: (DIFF-B4) mean_ret>0 且 t_NW>0，且 OOS 同号',
            'portfolio': '行业Top3 × 行业内Top20%，等权，k日信息选股，k+1开盘进场（§43/§44）'},
        'p4_G4_M2_breadth': {'h5_ALL': g4_all, 'h5_OOS': g4_oos},
        'p4_G5_M3_diffusion': {'h5_ALL': g5_all, 'h5_OOS': g5_oos},
        'p4_G20_DIFF_minus_B4': {'h5_ALL': g20_all, 'h5_OOS': g20_oos,
                                 'DIFF_h5_ALL': _bl('DIFF', 5, 'ALL'),
                                 'B4_h5_ALL': _bl('B4', 5, 'ALL')},
        'p4_rows': {'ic': len(ic_rows), 'auc': len(auc_rows), 'quantile': len(q_rows),
                    'ladder': len(ladder_rows), 'fm': len(fm_rows), 'baseline': len(base_rows)},
        'p4_elapsed_sec': round(time.time() - t_start, 1),
    })
    LOG('  [SAVE] _meta_p4.json')
    LOG.sep('=')
    LOG('DONE  P4  （总耗时 %.1fs）' % (time.time() - t_start))


if __name__ == '__main__':
    main()
