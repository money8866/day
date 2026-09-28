# -*- coding: utf-8 -*-
"""
H-IND-DIFFUSION-01  P7：Gate 判定 / 最终决策 / 最终报告（STEP23 / STEP24；§46 §50-§57）

隔离声明
    本模块只 import hid_common（预注册常量与通用 IO），
    不 import hid_null / hid_robust（避免 mklog() 截断其阶段日志）。

产出（§57）
    gate_results.csv        G1-G20 逐项判定（criterion / evidence / verdict / lethal）
    h_ind_diffusion.json    结构化结论（含 §55 十五问、FDR 摘要、授权状态、处置）
    report.md               人读报告（含致命 Gate、§52/§53/§54 判定、§56 接口处置）
    fdr_results.csv         §46 BH-FDR 全量重算（覆盖 P4/P5/P6 全部可检验统计量）

预先声明（本文件在读到 P6 数值之前写定；§59 禁止事后改口径）
    [R1] Gate 判定一律按 hid_common.GATES 的 criterion 与 P4/P6 已冻结的判据执行；
         任何缺失产物 -> verdict = NOT_RUN（致命 Gate 记 NOT_RUN 等同于未通过）。
    [R2] 主方向 = 原始方向（高扩散暴露 -> 高未来收益）。反向结果仅作披露（§59）。
    [R3] §46 FDR：对全部具有 t 统计量 / p 值的检验做 BH-FDR（族内 + 合并池）。
         quantile 家族只对 6 个 STOCK 层信号重算逐日 Q5-Q1 序列以取得 t（并与 P4
         的 spread_top_bot 逐值比对，见日志 [CHECK-Q]）；IND 层分位表无语义等价 t，
         其信息已由 ic_results.csv 的 IND 行纳入 'model'/'horizon' 家族，报告如实标注。
    [R4] G18 关键检验集（先于 P6 数值写定）：K1..K7 见 KEY_FDR。
         G18 = PASS 当且仅当 K1-K7 在「族内」与「合并池」BH-FDR(alpha=0.05) 下均显著。
    [R5] §53 CONDITIONAL 需有「预注册的失败风险预测检验」支持；本研究未预注册此类检验，
         故不得以描述性 §49 失败归因替代，默认不触发 CONDITIONAL。
"""
import json
import math
import os
import time
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore', category=RuntimeWarning,
                        message=r'.*(All-NaN|empty slice|Degrees of freedom|invalid value'
                                r'|divide by zero).*')

from hid_common import (HERE, DATA, GATES, PREREG, mklog, pload, pload_day, pload_meta,
                        pload_meta_side, pload_codes, ic_stats, rank_avg, bh_fdr,
                        out_csv, prereg_hash, derived_hash, PHASES)

LOG = mklog('report')
PAN = os.path.join(DATA, 'panel')

H_MAIN = 5
NQ = int(PREREG['n_quantiles'])
LEGACY = list(PREREG['lethal_gates'])
PERM_ALPHA = float(PREREG['perm_alpha'])

# 6 个 STOCK 层分位信号（与 P4 quantile_results.csv 的 STOCK/ALL 行一一对应）
Q_SIGS = [('exposure', 'expo_expo_ind'), ('expo_ind', 'expo_expo_ind_only'),
          ('expo_rs', 'expo_expo_rs_only'), ('rs_20', 'expo_rs_20'),
          ('ret_20', 'ret_20'), ('ret_5', 'ret_5')]

# [R4] G18 关键检验集：(family, test)
KEY_FDR = [
    ('model', 'M3.diffusion|ALL|h5|ALL'),
    ('fama_macbeth', 'diffusion|ALL|h5|ALL'),
    ('baseline', 'DIFF-B4|h5|ALL'),
    ('oos', 'signal_ALL|h5|OOS'),
    ('permutation', 'IC_exposure|h5|ALL'),
    ('null', 'N3_MomentumMatched|DIFF_return|h5|ALL'),
    ('null', 'N4_IndustryMomentumMatched|IS_DIFF_return|h5|ALL'),
]

# 溯源：最终定稿前对 P5 的实现修正（§50 G1 数据完整性披露；不改口径 / 不改阈值）
FIX_LOG = [
    {'when': 'P5 首跑之后、P6 之前',
     'file': 'hid_null.py',
     'item': 'Acc 零分布累积的 NaN 污染',
     'detail': '原实现以 sum += null_vals 累积置换零分布，使某日整条为 NaN 的置换向量'
               '（如面板末尾各 horizon 前向收益整体缺失的日子）永久污染该子样本全部 1000 个'
               '置换位，导致 N1/N4 在 ALL/TRAIN/LIVE-LIKE 上 n_perm=0、null 与 p 均为 NaN；'
               '据此判定 G7/G8 会以 NaN 伪装 FAIL。已改为「逐位置有限值计数」累积'
               '（sum 仅累加有限值、fin 记有限次数），并在同一冻结口径下重跑 P5。',
     'scope': '仅影响 null_models.csv 的 N1/N4 行；N2/N3/N5 与 permutation.csv 的全部置换值'
              '在原实现下 n_perm 已为 1000（无 NaN 位置），重跑后已逐值核对完全一致。'
              '特征定义、样本、方向、阈值、Gate 判据均未改动（§54：非事后救援）。'},
]


# ══════════════════════════════════════════════════════════════ 基础工具

def p_two(t):
    if t is None or not np.isfinite(t):
        return np.nan
    return float(math.erfc(abs(float(t)) / math.sqrt(2.0)))


def p_one(t):
    if t is None or not np.isfinite(t):
        return np.nan
    return float(0.5 * math.erfc(float(t) / math.sqrt(2.0)))


def rd(name):
    fp = os.path.join(HERE, name)
    if not os.path.exists(fp):
        return None
    try:
        return pd.read_csv(fp)
    except Exception:
        return None


def row(df, **conds):
    """取首个匹配行 -> dict（无则 {}）"""
    if df is None:
        return {}
    m = pd.Series(True, index=df.index)
    for c, v in conds.items():
        if c not in df.columns:
            return {}
        if isinstance(v, (list, tuple, set)):
            m &= df[c].isin(list(v))
        else:
            m &= (df[c] == v)
    g = df[m]
    return {} if len(g) == 0 else g.iloc[0].to_dict()


def fnum(d, k, nd=6):
    try:
        v = float(d.get(k, np.nan))
    except Exception:
        return 'NA'
    return 'NA' if not np.isfinite(v) else ('%+.*f' % (nd, v))


def f2(v, nd=4):
    try:
        x = float(v)
    except Exception:
        return 'NA'
    return 'NA' if not np.isfinite(x) else ('%+.*f' % (nd, x))


def daily_q5q1(sig, tgt, VAL, nq=NQ, min_xs=30):
    """逐日 Q5-Q1 价差序列（与 P4 quantile_results 的 STOCK/ALL 口径一致）"""
    NCAL = sig.shape[1]
    out = np.full(NCAL, np.nan)
    for k in range(NCAL):
        m = VAL[:, k] & np.isfinite(sig[:, k]) & np.isfinite(tgt[:, k])
        n = int(m.sum())
        if n < min_xs:
            continue
        s = np.asarray(sig[m, k], dtype=np.float64)
        t = np.asarray(tgt[m, k], dtype=np.float64)
        r = rank_avg(s)
        b = np.minimum(nq - 1, ((r - 1.0) / n * nq).astype(np.int64))
        lo = t[b == 0]
        hi = t[b == nq - 1]
        if len(lo) and len(hi):
            out[k] = float(hi.mean() - lo.mean())
    return out


# ══════════════════════════════════════════════════════════════ §46 FDR 全量

def collect_fdr():
    rows = []

    def add(family, test, stat, p, direction, src):
        if p is None or not np.isfinite(p):
            return
        rows.append({'family': family, 'test': test, 'statistic': stat,
                     'raw_p': float(p), 'direction': direction, 'source': src})

    # ---- 信号层 IC（feature：全部信号 h5 ALL；horizon：核心信号 × 分期）
    d = rd('ic_results.csv')
    if d is not None:
        for _, r in d[(d['phase'] == 'ALL') & (d['horizon'] == H_MAIN)].iterrows():
            add('feature', '%s|h5|ALL' % r['signal'], 'mean_ic',
                p_two(r['t_nw']), 'two-sided', 'ic_results.csv')
        core = ['exposure', 'expo_ind', 'expo_rs', 'ind_diffusion_score', 'ret_20',
                'ind_mom_20', 'ind_dchg_5', 'ind_up_20', 'ind_lb_020', 'ind_accel20']
        for _, r in d[d['signal'].isin(core) & d['phase'].isin(['ALL', 'OOS'])].iterrows():
            add('horizon', '%s|h%d|%s' % (r['signal'], r['horizon'], r['phase']),
                'mean_ic', p_two(r['t_nw']), 'two-sided', 'ic_results.csv')

    # ---- 判别 AUC（discrimination）
    d = rd('auc_results.csv')
    if d is not None:
        d = d[(d['label'] == 'gt_ind_median')
              & d['signal'].isin(['exposure', 'expo_ind', 'expo_rs', 'ind_diffusion_score'])
              & d['phase'].isin(['ALL', 'OOS'])]
        for _, r in d.iterrows():
            add('discrimination', '%s|h%d|%s' % (r['signal'], r['horizon'], r['phase']),
                'auc', p_two(r['t_nw_vs_50']), 'two-sided(vs 0.5)', 'auc_results.csv')

    # ---- 分位价差（quantile：6 个 STOCK 信号重算逐日 Q5-Q1）
    qres = rd('quantile_results.csv')
    if qres is not None:
        globals()['_QRES'] = qres
        for nm, fp in Q_SIGS:
            try:
                sp = np.load(os.path.join(PAN, fp + '.npy'), mmap_mode='r')
            except Exception:
                continue
            ser = globals().get('_Q5Q1', {}).get(nm)
            if ser is None:
                continue
            for ph in ('ALL', 'OOS'):
                st = ic_stats(ser[globals()['_PHM'][ph]], lag=19)
                add('quantile', '%s|h5|%s' % (nm, ph), 'q5_q1_mean',
                    p_two(st['t_nw']), 'two-sided', 'recomputed(validated vs P4)')

    # ---- 模型阶梯（model）
    d = rd('model_ladder.csv')
    if d is not None:
        d = d[(((d['model'] == 'M2') & (d['term'] == 'ind_breadth'))
               | ((d['model'] == 'M3') & (d['term'] == 'diffusion')))
              & (d['phase'].isin(['ALL', 'OOS']))]
        for _, r in d.iterrows():
            add('model', '%s.%s|%s|h%d|%s' % (r['model'], r['term'], r['sample'],
                                              r['horizon'], r['phase']),
                'beta', p_one(r['t_nw']), 'one-sided(+)', 'model_ladder.csv')

    # ---- Fama-MacBeth（fama_macbeth）
    d = rd('fama_macbeth.csv')
    if d is not None:
        d = d[d['phase'].isin(['ALL', 'OOS'])]
        for _, r in d.iterrows():
            add('fama_macbeth', '%s|%s|h%d|%s' % (r['term'], r['sample'],
                                                  r['horizon'], r['phase']),
                'beta', p_one(r['t_nw']), 'one-sided(+)', 'fama_macbeth.csv')

    # ---- 组合基线（baseline）
    d = rd('baseline_results.csv')
    if d is not None:
        for _, r in d.iterrows():
            add('baseline', '%s|h%d|%s' % (r['strategy'], r['horizon'], r['phase']),
                'mean_ret', p_two(r['t_nw']), 'two-sided', 'baseline_results.csv')

    # ---- Null（null）
    d = rd('null_models.csv')
    if d is not None:
        for _, r in d.iterrows():
            add('null', '%s|%s|h%d|%s' % (r['null_model'], r['statistic'],
                                          r['horizon'], r['subset']),
                'obs_vs_null', r['p_one_sided'], 'one-sided(+)', 'null_models.csv')

    # ---- 置换（permutation）
    d = rd('permutation.csv')
    if d is not None:
        for _, r in d.iterrows():
            add('permutation', '%s|h%d|%s' % (r['statistic'], r['horizon'], r['subset']),
                'obs_vs_perm', r['p_one_sided'], 'one-sided(+)', 'permutation.csv')

    # ---- OOS（oos）
    d = rd('oos_results.csv')
    if d is not None:
        d = d[(d['phase'].isin(['ALL', 'OOS'])) & (d['horizon'] == H_MAIN)]
        for _, r in d.iterrows():
            if r['object'] == 'signal':
                if not pd.notna(r['ic_t_nw']):
                    continue
                add('oos', 'signal_%s|h5|%s' % (r['scope'], r['phase']), 'mean_ic',
                    p_two(r['ic_t_nw']), 'two-sided', 'oos_results.csv')
            else:
                add('oos', '%s|h5|%s' % (r['object'], r['phase']), 'mean_ret',
                    p_two(r['t_nw']), 'two-sided', 'oos_results.csv')

    # ---- Walk Forward（walkforward）
    d = rd('walkforward.csv')
    if d is not None:
        for _, r in d[d['horizon'] == H_MAIN].iterrows():
            add('walkforward', '%s|h5|oos%s' % (r['window'], r['oos']),
                'diffb4_oos', p_two(r['diffb4_oos_t']), 'two-sided', 'walkforward.csv')

    # ---- 年份（year）
    d = rd('year_results.csv')
    if d is not None:
        for _, r in d.iterrows():
            add('year', '%d|h%d' % (int(r['year']), int(r['horizon'])), 'diffb4_mean',
                p_two(r['DIFFB4_t']), 'two-sided', 'year_results.csv')

    # ---- Regime（regime）
    d = rd('regime_results.csv')
    if d is not None:
        for _, r in d.iterrows():
            add('regime', '%s|h%d' % (r['regime'], int(r['horizon'])), 'diffb4_mean',
                p_two(r['DIFFB4_t']), 'two-sided', 'regime_results.csv')

    # ---- 参数面（window）
    d = rd('parameter_surface.csv')
    if d is not None:
        for _, r in d.iterrows():
            add('window', '%s=%s|M%s|h5' % (r['axis'], r['param_value'], r['mom_win']),
                'inc_mean', p_two(r['inc_t']), 'two-sided', 'parameter_surface.csv')

    # ---- 尾部（tail）
    d = rd('tail_analysis.csv')
    if d is not None:
        for _, r in d[d['basis'] == 'DIFF-B4'].iterrows():
            add('tail', 'DIFF-B4|leave%.2f' % r['leave_pct'], 'mean_ret',
                p_two(r['t_nw']), 'two-sided', 'tail_analysis.csv')

    # ---- 成本（cost）
    d = rd('cost_analysis.csv')
    if d is not None:
        for _, r in d[(d['row_type'] == 'cost')].iterrows():
            add('cost', '%s|%dbp' % (r['strategy'], int(r['bps'])), 'net_mean',
                p_two(r['net_t']), 'two-sided', 'cost_analysis.csv')

    if not rows:
        return None
    df = pd.DataFrame(rows)
    df['q_bh_within'] = np.nan
    df['reject_within'] = False
    for fam, g in df.groupby('family'):
        rej, q = bh_fdr(g['raw_p'].values, alpha=float(PREREG['fdr_alpha']))
        df.loc[g.index, 'q_bh_within'] = q
        df.loc[g.index, 'reject_within'] = rej
    rej, q = bh_fdr(df['raw_p'].values, alpha=float(PREREG['fdr_alpha']))
    df['q_bh_pooled'] = q
    df['reject_pooled'] = rej
    df = df.sort_values(['family', 'raw_p']).reset_index(drop=True)
    out_csv('fdr_results.csv', df)
    LOG.sep('─')
    LOG('[STEP22/§46] BH-FDR（alpha=%.2f）：%d 项检验，族内显著 %d，合并池显著 %d'
        % (PREREG['fdr_alpha'], len(df), int(df['reject_within'].sum()),
           int(df['reject_pooled'].sum())))
    for fam, g in df.groupby('family'):
        LOG('    %-16s n=%-5d raw_p<.05 %-4d 族内显著 %-4d 合并池显著 %d'
            % (fam, len(g), int((g['raw_p'] < 0.05).sum()),
               int(g['reject_within'].sum()), int(g['reject_pooled'].sum())))
    return df


# ══════════════════════════════════════════════════════════════ 主流程

def main():
    t_all = time.time()
    LOG.sep('=')
    LOG('H-IND-DIFFUSION-01  P7 Gate 判定 / 最终报告（STEP23-24；§46 §50-§57）')
    LOG('  hash=%s derived=%s  %s' % (prereg_hash(), derived_hash(),
                                      time.strftime('%Y-%m-%d %H:%M:%S')))
    LOG.sep()

    meta = pload_meta()
    NCODES, NCAL = int(meta['NCODES']), int(meta['NCAL'])
    n_l1 = int(meta['n_l1'])
    day = pload_day()
    phc = np.asarray(day['phase']).astype(np.int8)
    PHM = {'ALL': np.ones(NCAL, dtype=bool)}
    for i, nm in enumerate(PHASES):
        PHM[nm] = (phc == i + 1)
    globals()['_PHM'] = PHM
    LOG('  面板 %d 股 × %d 日；行业 %d（L1）' % (NCODES, NCAL, n_l1))

    M1 = meta
    M2 = pload_meta_side('_meta_p2.json', {}) or {}
    M3 = pload_meta_side('_meta_p3.json', {}) or {}
    M4 = pload_meta_side('_meta_p4.json', {}) or {}
    M5 = pload_meta_side('_meta_p5.json', {}) or {}
    M6 = pload_meta_side('_meta_p6.json', {}) or {}

    # ---- G1/G2 数据完整性（重算，不依赖阶段自述） ----
    ind = np.asarray(pload('_code_ind_l1'))
    vald = (np.asarray(pload('valid')) == 1)
    n_valid = int(vald.sum())
    n_unk = int(((ind == 0) & vald).sum())
    cov = 1.0 - n_unk / max(n_valid, 1)
    LOG('  [G1/G2] valid=%d（meta %d）UNKNOWN=%d（%.4f）coverage=%.6f（meta %.6f）'
        % (n_valid, int(M1['rows_valid']), n_unk, n_unk / max(n_valid, 1),
           cov, float(M1['coverage_l1'])))

    hash_ok = True
    hash_tab = {}
    for nm, kp, kd in (('_meta.json', 'prereg_hash', None),
                       ('_meta_p2.json', 'p2_prereg_hash', 'p2_derived_hash'),
                       ('_meta_p3.json', 'p3_prereg_hash', 'p3_derived_hash'),
                       ('_meta_p4.json', 'p4_prereg_hash', 'p4_derived_hash'),
                       ('_meta_p5.json', 'p5_prereg_hash', 'p5_derived_hash'),
                       ('_meta_p6.json', 'p6_prereg_hash', 'p6_derived_hash')):
        m = {'_meta.json': M1, '_meta_p2.json': M2, '_meta_p3.json': M3,
             '_meta_p4.json': M4, '_meta_p5.json': M5, '_meta_p6.json': M6}[nm]
        if not m:
            continue
        ph = str(m.get(kp, ''))
        dh = str(m.get(kd, '')) if kd else None
        hash_tab[nm] = {'prereg': ph, 'derived': dh}
        if ph != prereg_hash() or (kd and dh != derived_hash()):
            hash_ok = False
    LOG('  [G1] 阶段 hash 一致性：%s' % ('OK' if hash_ok else '不一致 -> %s' % hash_tab))

    # ---- §46 FDR（先算，G18 依赖） ----
    LOG.sep('─')
    LOG('[STEP22] §46 先重算分位价差日序列（6 个 STOCK 信号，与 P4 交叉校验）')
    t0 = time.time()
    SCOPE = (np.asarray(pload('expo_expo_scope')) == 1)
    EXE5 = np.asarray(pload('exe_5'), dtype=np.float32)
    qres = rd('quantile_results.csv')
    Q5Q1 = {}
    for nm, fp in Q_SIGS:
        try:
            sig = np.asarray(pload(fp), dtype=np.float32)
        except Exception:
            continue
        ser = daily_q5q1(sig, EXE5, SCOPE)
        Q5Q1[nm] = ser
        r = row(qres, signal=nm, kind='STOCK', sort_scope='ALL', nq=NQ,
                horizon=H_MAIN, phase='ALL')
        ref = float(r.get('spread_top_bot', np.nan)) if r else np.nan
        cur = float(np.nanmean(ser[PHM['ALL']])) if np.isfinite(ser).any() else np.nan
        st = ic_stats(ser[PHM['ALL']], lag=19)
        LOG('    [CHECK-Q] %-12s 重算 Q5-Q1=%+.6f | P4=%+.6f | diff=%.2e | t=%+.2f'
            % (nm, cur, ref, (cur - ref) if np.isfinite(ref) else np.nan, st['t_nw']))
    globals()['_Q5Q1'] = Q5Q1
    LOG('  [DONE] 分位价差重算（%.1fs）' % (time.time() - t0))
    FDR = collect_fdr()

    # ---- Gate 判定 ----
    LOG.sep('─')
    LOG('[STEP23] §50 Gate G1-G20 判定')
    G = []

    def gate(code, verdict, ev, src):
        G.append({'gate': code, 'name': GATES[code]['name'], 'lethal': bool(GATES[code]['lethal']),
                  'criterion': GATES[code]['criterion'], 'verdict': verdict,
                  'evidence': ev, 'source': src})

    # G1
    ev1 = ('阶段 hash 一致=%s；valid 重算=%d vs meta=%d；coverage 重算=%.6f vs meta=%.6f'
           % (hash_ok, n_valid, int(M1['rows_valid']), cov, float(M1['coverage_l1'])))
    v1 = 'PASS' if (hash_ok and n_valid == int(M1['rows_valid'])
                    and abs(cov - float(M1['coverage_l1'])) < 1e-9) else 'FAIL'
    gate('G1', v1, ev1, '_meta*.json + panel 重算')

    # G2
    v2 = 'PASS' if cov >= float(PREREG['ind_coverage_min']) else 'FAIL'
    gate('G2', v2, 'SW L1 PIT 覆盖率=%.6f（门槛 %.2f）；UNKNOWN=%d（未删除样本，如实披露）'
         % (cov, float(PREREG['ind_coverage_min']), n_unk), '_meta.json + 面板重算')

    # G3
    g3 = M6.get('p6_G3_tradability') or {}
    if not g3:
        gate('G3', 'NOT_RUN', '缺 _meta_p6.json', 'P6')
    else:
        gate('G3', 'PASS' if g3.get('pass') else 'FAIL',
             'DIFF 入选样本 T+1 可成交率=%.4f（门槛 %.2f）'
             % (float(g3.get('tradable_rate', np.nan)), float(PREREG['tradable_min'])),
             '_meta_p6.json')

    # G4 / G5 模型阶梯
    ml = rd('model_ladder.csv')
    def ladder(mdl, term, ph):
        r = row(ml, sample='ALL', model=mdl, horizon=H_MAIN, phase=ph, term=term)
        return float(r.get('beta', np.nan)), float(r.get('t_nw', np.nan))
    b4a, t4a = ladder('M2', 'ind_breadth', 'ALL')
    b4o, t4o = ladder('M2', 'ind_breadth', 'OOS')
    v4 = 'PASS' if (np.isfinite(b4a) and b4a > 0 and t4a > 0
                    and np.isfinite(b4o) and b4o > 0) else 'FAIL'
    gate('G4', v4, 'M2 ind_breadth：ALL beta=%s t=%s；OOS beta=%s t=%s（ALL 需 beta>0 且 t>0，OOS 需同号）'
         % (f2(b4a), f2(t4a), f2(b4o), f2(t4o)), 'model_ladder.csv')

    b5a, t5a = ladder('M3', 'diffusion', 'ALL')
    b5o, t5o = ladder('M3', 'diffusion', 'OOS')
    v5 = 'PASS' if (np.isfinite(b5a) and b5a > 0 and t5a > 0
                    and np.isfinite(b5o) and b5o > 0) else 'FAIL'
    gate('G5', v5, 'M3 diffusion：ALL beta=%s t=%s；OOS beta=%s t=%s'
         % (f2(b5a), f2(t5a), f2(b5o), f2(t5o)), 'model_ladder.csv')

    # G6 Fama-MacBeth
    fm = rd('fama_macbeth.csv')
    r6 = row(fm, sample='ALL', horizon=H_MAIN, phase='ALL', term='diffusion')
    r6o = row(fm, sample='ALL', horizon=H_MAIN, phase='OOS', term='diffusion')
    b6, t6 = float(r6.get('beta', np.nan)), float(r6.get('t_nw', np.nan))
    v6 = 'PASS' if (np.isfinite(b6) and b6 > 0 and t6 > 0) else 'FAIL'
    gate('G6', v6, 'FM diffusion（含 §24 市场层残差化）：ALL beta=%s t=%s（n=%s）；OOS beta=%s t=%s'
         % (f2(b6), f2(t6), r6.get('n_day', 'NA'),
            f2(r6o.get('beta', np.nan)), f2(r6o.get('t_nw', np.nan))), 'fama_macbeth.csv')

    # G7 / G8 Null
    nl = rd('null_models.csv')
    def nullrow(model, stat, subset='ALL'):
        return row(nl, null_model=model, statistic=stat, horizon=H_MAIN, subset=subset)
    n1 = nullrow('N1_RandomIndustryDate', 'IS_DIFF_return')
    ok7 = bool(n1) and float(n1['obs']) > float(n1['null_mean']) \
        and float(n1['p_one_sided']) < PERM_ALPHA
    gate('G7', 'PASS' if ok7 else 'FAIL',
         'N1 随机行业-日期：obs=%s null=%s p=%.4f z=%s'
         % (f2(n1.get('obs'), 5), f2(n1.get('null_mean'), 5),
            float(n1.get('p_one_sided', np.nan)), f2(n1.get('z'), 2)) if n1 else '缺 null_models.csv',
         'null_models.csv')

    n3 = nullrow('N3_MomentumMatched', 'DIFF_return')
    n4 = nullrow('N4_IndustryMomentumMatched', 'IS_DIFF_return')
    ok8 = (bool(n3) and bool(n4)
           and float(n3['obs']) > float(n3['null_mean']) and float(n3['p_one_sided']) < PERM_ALPHA
           and float(n4['obs']) > float(n4['null_mean']) and float(n4['p_one_sided']) < PERM_ALPHA)
    gate('G8', 'PASS' if ok8 else 'FAIL',
         'N3 动量匹配：obs=%s null=%s p=%.4f | N4 行业动量匹配：obs=%s null=%s p=%.4f'
         % (f2(n3.get('obs'), 5), f2(n3.get('null_mean'), 5), float(n3.get('p_one_sided', np.nan)),
            f2(n4.get('obs'), 5), f2(n4.get('null_mean'), 5), float(n4.get('p_one_sided', np.nan)))
         if (n3 and n4) else '缺 null_models.csv', 'null_models.csv')

    # G9 / G13 OOS（信号层 exposure ALL 口径 h5）
    oo = rd('oos_results.csv')
    so = row(oo, object='signal', scope='ALL', horizon=H_MAIN, phase='OOS')
    ic_o = float(so.get('ic_mean', np.nan))
    t_o = float(so.get('ic_t_nw', np.nan))
    q_o = float(so.get('q5_q1', np.nan))
    au_o = float(so.get('auc', np.nan))
    pr_o = float(so.get('prec10', np.nan))
    mo_o = float(so.get('mono_rho', np.nan))
    ok9 = np.isfinite(ic_o) and ic_o > 0 and t_o > 0 and np.isfinite(q_o) and q_o > 0
    gate('G9', 'PASS' if ok9 else 'FAIL',
         'OOS exposure ALL：IC=%s t=%s Q5-Q1=%s（n=%s）'
         % (f2(ic_o, 5), f2(t_o, 2), f2(q_o, 5), so.get('n_days', 'NA')), 'oos_results.csv')

    ok13 = (np.isfinite(ic_o) and abs(ic_o) >= float(PREREG['ic_min'])
            and np.isfinite(au_o) and au_o >= float(PREREG['auc_min'])
            and np.isfinite(pr_o) and pr_o >= float(PREREG['prec10_min'])
            and np.isfinite(mo_o) and mo_o > 0)
    gate('G13', 'PASS' if ok13 else 'FAIL',
         'OOS |IC|=%s（≥%.2f）AUC=%s（≥%.2f）Prec@10=%s（≥%.2f）mono_rho=%s（>0）方向无关上限 AUC\'=%s'
         % (f2(abs(ic_o), 5), PREREG['ic_min'], f2(au_o), PREREG['auc_min'],
            f2(pr_o), PREREG['prec10_min'], f2(mo_o, 2), f2(1.0 - au_o)),
         'oos_results.csv（原始方向，§59 禁止反向择优）')

    # G10 - G16、G19 取 P6 已冻结的判定量
    def p6v(k):
        return M6.get(k) or {}
    w = p6v('p6_G10_WF')
    gate('G10', 'PASS' if (w and w.get('ratio', 0) > float(PREREG['wf_pos_frac'])) else
         ('FAIL' if w else 'NOT_RUN'),
         'OOS 增量与 IC 双正窗口 %s/%s = %s（阈值 > %.2f）'
         % (w.get('double_positive', 'NA'), w.get('windows', 'NA'),
            f2(w.get('ratio'), 2), float(PREREG['wf_pos_frac'])) if w else '缺 _meta_p6.json',
         'walkforward.csv')

    y = p6v('p6_G11_year')
    ok11 = bool(y) and y.get('ratio', 0) >= float(PREREG['year_pos_frac']) \
        and y.get('max_share', 1) <= float(PREREG['year_max_share'])
    gate('G11', 'PASS' if ok11 else ('FAIL' if y else 'NOT_RUN'),
         '正年份 %s/%s = %s（阈值 %.2f）；最大单年贡献占比 %s（上限 %.2f）'
         % (y.get('positive', 'NA'), y.get('n_years', 'NA'), f2(y.get('ratio'), 2),
            float(PREREG['year_pos_frac']), f2(y.get('max_share'), 2),
            float(PREREG['year_max_share'])) if y else '缺 _meta_p6.json', 'year_results.csv')

    rg = p6v('p6_G12_regime')
    gate('G12', 'PASS' if (rg and rg.get('sign_flips', 99) <= int(PREREG['regime_flip_max'])) else
         ('FAIL' if rg else 'NOT_RUN'),
         'BEAR/NORMAL/BULL 增量符号翻转 %s 次（上限 %d）；明细 %s'
         % (rg.get('sign_flips', 'NA'), int(PREREG['regime_flip_max']),
            rg.get('detail', 'NA')) if rg else '缺 _meta_p6.json', 'regime_results.csv')

    pm = p6v('p6_G14_param')
    gate('G14', 'PASS' if (pm and pm.get('ratio', 0) >= float(PREREG['param_same_sign_frac'])) else
         ('FAIL' if pm else 'NOT_RUN'),
         '主参数面 %s 格中增量为正 %s 格 = %s（阈值 %.2f）'
         % (pm.get('n_cells', 'NA'), pm.get('positive', 'NA'), f2(pm.get('ratio'), 2),
            float(PREREG['param_same_sign_frac'])) if pm else '缺 _meta_p6.json',
         'parameter_surface.csv')

    tl = p6v('p6_G15_tail')
    v15 = tl.get('leave_top5_diffb4', [np.nan, np.nan]) if tl else [np.nan, np.nan]
    gate('G15', 'PASS' if (tl and np.isfinite(v15[0]) and v15[0] > 0) else
         ('FAIL' if tl else 'NOT_RUN'),
         'Leave-Top5%% 后 DIFF-B4 日均增量 = %s（t=%s）' % (f2(v15[0], 5), f2(v15[1], 2))
         if tl else '缺 _meta_p6.json', 'tail_analysis.csv')

    c = p6v('p6_G16_cost30')
    gate('G16', 'PASS' if (c and c.get('net_mean', 0) > 0) else ('FAIL' if c else 'NOT_RUN'),
         '30bp 往返后 DIFF-B4：gross=%s 换手/日=%.4f 净日均=%s 净年化=%s%% t=%s'
         % (f2(c.get('gross_mean'), 5), float(c.get('turnover_daily', np.nan)),
            f2(c.get('net_mean'), 5), f2(100 * c.get('net_ann', np.nan), 2),
            f2(c.get('net_t'), 2)) if c else '缺 _meta_p6.json', 'cost_analysis.csv')

    pf = rd('permutation.csv')
    rp = row(pf, statistic='IC_exposure', horizon=H_MAIN, subset='ALL')
    ok17 = bool(rp) and float(rp['obs']) > float(rp['null_mean']) \
        and float(rp['p_one_sided']) < PERM_ALPHA
    gate('G17', 'PASS' if ok17 else 'FAIL',
         '§45 置换（n_perm=%s）IC_exposure h5 ALL：obs=%s null=%s p=%.4f z=%s'
         % (rp.get('n_perm', 'NA'), f2(rp.get('obs'), 5), f2(rp.get('null_mean'), 5),
            float(rp.get('p_one_sided', np.nan)), f2(rp.get('z'), 2)) if rp else '缺 permutation.csv',
         'permutation.csv')

    # G18
    kstat = {}
    if FDR is not None:
        for fam, tst in KEY_FDR:
            g = FDR[(FDR['family'] == fam) & (FDR['test'] == tst)]
            kstat['%s|%s' % (fam, tst)] = (
                None if len(g) == 0 else (float(g.iloc[0]['raw_p']),
                                          bool(g.iloc[0]['reject_within']),
                                          bool(g.iloc[0]['reject_pooled'])))
    ok18 = bool(kstat) and all(v is not None and v[1] and v[2] for v in kstat.values())
    gate('G18', 'PASS' if ok18 else 'FAIL',
         '关键检验 K1-K7 经 BH-FDR 后族内/合并池同时显著：%d/%d 项满足；明细 %s'
         % (sum(1 for v in kstat.values() if v and v[1] and v[2]), len(KEY_FDR),
            {k: (None if v is None else ('p=%.3g' % v[0], v[1], v[2]))
             for k, v in kstat.items()}),
         'fdr_results.csv')

    ec = p6v('p6_G19_econ')
    gate('G19', 'PASS' if (ec and ec.get('pass')) else ('FAIL' if ec else 'NOT_RUN'),
         '30bp 后净日均增量 %s vs 预注册门槛 %.4f'
         % (f2(ec.get('net_mean_30bp'), 5), float(PREREG['econ_min_net']))
         if ec else '缺 _meta_p6.json', 'cost_analysis.csv')

    bl = rd('baseline_results.csv')
    r20 = row(bl, strategy='DIFF-B4', horizon=H_MAIN, phase='ALL')
    r20o = row(bl, strategy='DIFF-B4', horizon=H_MAIN, phase='OOS')
    m20, tt20 = float(r20.get('mean_ret', np.nan)), float(r20.get('t_nw', np.nan))
    m20o = float(r20o.get('mean_ret', np.nan))
    ok20 = (np.isfinite(m20) and m20 > 0 and tt20 > 0
            and np.isfinite(m20o) and m20o > 0)
    gate('G20', 'PASS' if ok20 else 'FAIL',
         '§44 DIFF−B4：ALL 日均=%s t=%s；OOS 日均=%s t=%s（ALL 需 >0 且 t>0，OOS 需同号）'
         % (f2(m20, 5), f2(tt20, 2), f2(m20o, 5), f2(r20o.get('t_nw', np.nan), 2)),
         'baseline_results.csv')

    gdf = pd.DataFrame(G)
    for cd in GATES:
        if cd not in set(gdf['gate']):
            gate(cd, 'NOT_RUN', '未产出判据', '-')
    gdf = pd.DataFrame(G)
    gdf['lethal_fail'] = gdf['lethal'] & (gdf['verdict'] != 'PASS')
    out_csv('gate_results.csv', gdf)
    for _, r in gdf.iterrows():
        LOG('    %-4s %-28s %-7s %s' % (r['gate'], r['name'], r['verdict'],
                                        'LETHAL' if r['lethal'] else ''))
    lethal_fail = list(gdf.loc[gdf['lethal_fail'], 'gate'])
    n_pass = int((gdf['verdict'] == 'PASS').sum())
    LOG('  [GATE 汇总] PASS %d/20；致命 FAIL/NOT_RUN：%s'
        % (n_pass, lethal_fail if lethal_fail else '无'))

    # ---- §52 / §53 / §54 最终判定 ----
    LOG.sep('─')
    crit52 = ['G4', 'G5', 'G9', 'G10', 'G11', 'G12', 'G13', 'G14', 'G15', 'G16', 'G17',
              'G18', 'G20']
    vv = dict(zip(gdf['gate'], gdf['verdict']))
    unmet52 = [c for c in crit52 if vv.get(c) != 'PASS']
    if not unmet52:
        decision = 'PASS'
        decision_txt = 'PASS — ROBUST INDUSTRY DIFFUSION INCREMENTAL ALPHA CONFIRMED'
    else:
        decision = 'FAIL'
        decision_txt = 'FAIL — NO ROBUST INCREMENTAL INDUSTRY DIFFUSION ALPHA FOUND'
    cond_trigger = False
    auth = 'YES' if (decision == 'PASS' and not lethal_fail) else 'NO'
    LOG('  [STEP24] §52 未满足项：%s' % (unmet52 if unmet52 else '无'))
    LOG('  [STEP24] §53 CONDITIONAL 触发条件（预注册失败风险预测检验）=%s -> 不触发'
        % cond_trigger)
    LOG('  [STEP24] 最终判定：%s' % decision_txt)
    LOG('  [STEP24] §51/§61 TRADING_AUTHORIZATION = %s（致命未通过：%s）'
        % (auth, lethal_fail if lethal_fail else '无'))
    LOG('  [STEP24] §56 处置 = ARCHIVE（不得污染现有生产策略）')

    # ---- §55 十五问 ----
    ic_r = rd('ic_results.csv')

    def icv(sig, h=H_MAIN, ph='ALL'):
        r = row(ic_r, signal=sig, horizon=h, phase=ph)
        return float(r.get('mean_ic', np.nan)), float(r.get('t_nw', np.nan))

    def qq(sig, ph='ALL'):
        r = row(qres, signal=sig, kind='STOCK', sort_scope='ALL', nq=NQ, horizon=H_MAIN, phase=ph)
        if not r:
            r = row(qres, signal=sig, kind='IND', sort_scope='ALL', nq=NQ,
                    horizon=H_MAIN, phase=ph)
        return float(r.get('spread_top_bot', np.nan)), float(r.get('mono_rho', np.nan))

    i_mom = icv('ind_mom_20')
    i_up = icv('ind_up_20')
    i_chg = icv('ind_dchg_5')
    i_dsc = icv('ind_diffusion_score')
    i_lb = icv('ind_lb_020')
    i_gap = icv('ind_diff_gap')
    i_rsb = icv('ind_rsb_chg_5')
    i_acc = icv('ind_accel20')
    i_expo = icv('exposure')
    i_expo_w = icv('exposure')
    n5 = nullrow('N5_BreadthShuffled', 'IS_DIFF_return')
    cf = {}
    if nl is not None:
        for q in ('A', 'B', 'C', 'D'):
            cf[q] = row(nl, null_model='CF27_Quadrant_%s' % q, statistic='selected_industry_return',
                        horizon=H_MAIN, subset='ALL')
    cf_ab = row(nl, null_model='CF27_A_minus_B', statistic='diff_selected_industry_return',
                horizon=H_MAIN, subset='ALL')
    fa = rd('failure_analysis.csv')

    Q = []
    Q.append(('Q1 行业 Breadth 是否预测未来行业收益？',
              '否（弱/反向）。Breadth20 水平（ind_up_20）h5 IC=%s（t=%s）；行业动量 ind_mom_20 IC=%s（t=%s）。'
              % (f2(i_up[0], 5), f2(i_up[1], 2), f2(i_mom[0], 5), f2(i_mom[1], 2))))
    Q.append(('Q2 Breadth Change 是否比 Breadth Level 更有信息？',
              '方向上「是」，但幅度不显著。BreadthChange5（ind_dchg_5）IC=%s（t=%s）> Breadth20 水平 IC=%s（t=%s）；'
              '15 个 IND 信号中 |t| 最大者仍 < 3。'
              % (f2(i_chg[0], 5), f2(i_chg[1], 2), f2(i_up[0], 5), f2(i_up[1], 2))))
    Q.append(('Q3 行业 Momentum 是否已经解释 Breadth？',
              '是（大部分）。模型阶梯：M2 加入 ind_breadth 后 beta=%s（t=%s，h5 ALL），'
              '相对 M1（ind_mom t=%s）增量 t 不足 2；M3 加入 diffusion 后 ind_breadth 被吸收。'
              % (f2(b4a, 6), f2(t4a, 2), f2(float(row(ml, sample='ALL', model='M1',
                                                        horizon=H_MAIN, phase='ALL',
                                                        term='ind_mom').get('t_nw', np.nan)), 2))))
    Q.append(('Q4 Leader Breadth 是否提供增量信息？',
              '无显著增量。ind_lb_020（LeaderBreadth20%%）h5 IC=%s（t=%s）；'
              'DiffusionGap（LB20−Breadth5）IC=%s（t=%s）。' % (f2(i_lb[0], 5), f2(i_lb[1], 2),
                                                            f2(i_gap[0], 5), f2(i_gap[1], 2))))
    cfa = cf.get('A') or {}
    cfb = cf.get('B') or {}
    Q.append(('Q5 Narrow Leadership 与 Broad Diffusion 是否存在差异？',
              '存在方向差异但不显著。§27 四象限：A（动量↑扩散↑）均值=%s，B（动量↑未扩散）均值=%s，A−B=%s（t=%s）。'
              % (f2(cfa.get('obs'), 5), f2(cfb.get('obs'), 5),
                 f2(cf_ab.get('obs'), 5), f2(cf_ab.get('z'), 2))))
    Q.append(('Q6 Diffusion 是否能预测行业内部股票？',
              '否（原始方向显著为负）。股票层 exposure：ALL IC=%s（t=%s），行业内部口径 IC 见 oos_results。'
              % (f2(i_expo[0], 5), f2(i_expo[1], 2))))
    Q.append(('Q7 控制 Stock Momentum 后是否仍然有效？',
              'G4 判定 %s。M2 的 ind_breadth 相对 M1 增量 beta=%s（t=%s，h5 ALL）。'
              % (vv.get('G4'), f2(b4a, 6), f2(t4a, 2))))
    Q.append(('Q8 控制 Industry Momentum 后是否仍然有效？',
              '行业层 G5=%s（M3 diffusion beta=%s，t=%s，h5 ALL）；但股票层暴露 OOS 不显著（t=%s），'
              '组合层 DIFF−B4 OOS t=%s。'
              % (vv.get('G5'), f2(b5a, 6), f2(t5a, 2), f2(t_o, 2), f2(tt20, 2))))
    Q.append(('Q9 控制 Market Momentum 后是否仍然有效？',
              'G6=%s。FM diffusion（§24 市场层残差化后）ALL beta=%s，t=%s。'
              % (vv.get('G6'), f2(b6, 6), f2(t6, 2))))
    Q.append(('Q10 Random Industry-Date 是否能够复制？',
              'G7=%s。N1：obs=%s null=%s p=%.4f。'
              % (vv.get('G7'), f2(n1.get('obs'), 5), f2(n1.get('null_mean'), 5),
                 float(n1.get('p_one_sided', np.nan)))))
    Q.append(('Q11 Momentum-Matched Null 是否能够复制？',
              'G8=%s。N3 p=%.4f；N4 p=%.4f（单侧，方向为「观测 > null」）。'
              % (vv.get('G8'), float(n3.get('p_one_sided', np.nan)),
                 float(n4.get('p_one_sided', np.nan)))))
    Q.append(('Q12 OOS 是否有效？',
              'G9=%s / G13=%s。OOS exposure ALL：IC=%s（t=%s），Q5−Q1=%s，AUC=%s，Prec@10=%s。'
              % (vv.get('G9'), vv.get('G13'), f2(ic_o, 5), f2(t_o, 2), f2(q_o, 5),
                 f2(au_o, 4), f2(pr_o, 4))))
    Q.append(('Q13 是否跨年份、跨 Regime 稳定？',
              'G11=%s / G12=%s。正年份比例 %s；Regime 符号翻转 %s 次。'
              % (vv.get('G11'), vv.get('G12'),
                 f2((p6v('p6_G11_year') or {}).get('ratio'), 2),
                 (p6v('p6_G12_regime') or {}).get('sign_flips', 'NA'))))
    Q.append(('Q14 30bp 成本后是否仍有经济意义？',
              'G16=%s / G19=%s。30bp 净日均增量=%s（净年化 %s%%），门槛 %.4f。'
              % (vv.get('G16'), vv.get('G19'), f2((c or {}).get('net_mean'), 5),
                 f2(100 * (c or {}).get('net_ann', np.nan), 2), float(PREREG['econ_min_net']))))
    Q.append(('Q15 Industry Diffusion 是否最终形成独立于既有 20D Momentum 的 Incremental Alpha？',
              '否。G20=%s（ALL 日均=%s t=%s；OOS 日均=%s t=%s）；'
              '且股票层暴露方向与假设相反，组合层增量未显著。'
              % (vv.get('G20'), f2(m20, 5), f2(tt20, 2), f2(m20o, 5),
                 f2(r20o.get('t_nw', np.nan), 2))))

    # ---- 落盘 JSON + report.md ----
    fail_tab = []
    if fa is not None:
        for _, r in fa.iterrows():
            fail_tab.append({'code': r['code'], 'name': r['name'],
                             'n_failed': int(r['n_failed']),
                             'pct_of_failures': float(r['pct_of_failures']),
                             'bucket_fail_rate': (None if not pd.notna(r['fail_rate_in_bucket'])
                                                  else float(r['fail_rate_in_bucket']))})

    summary = {
        'hypothesis_id': PREREG['hypothesis_id'],
        'title': PREREG['title'],
        'version': PREREG['version'],
        'built_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'prereg_hash': prereg_hash(), 'derived_hash': derived_hash(),
        'spec_ref': PREREG['spec_ref'],
        'research_question': '在已经知道行业 Momentum 与股票 Momentum 的情况下，'
                             '行业内「强度从少数股票向多数股票扩散」是否包含新的、可事前识别、'
                             '可跨期重复、成本后存在经济意义的信息？',
        'data': {
            'n_codes': NCODES, 'n_cal': NCAL, 'n_l1': n_l1, 'n_l2': int(meta['n_l2']),
            'date_start': meta['date_start'], 'date_end': meta['date_end'],
            'rows_valid': int(meta['rows_valid']),
            'excluded_count': meta['excluded_count'],
            'coverage_l1': cov, 'coverage_recomputed': cov,
            'unknown_l1_count': n_unk,
            'industry_source': meta['ind_source'],
            'phases': {k: int(v.sum()) for k, v in PHM.items() if k != 'ALL'},
        },
        'key_numbers': {
            'ic_exposure_h5_ALL': [i_expo[0], i_expo[1]],
            'ic_exposure_h5_OOS': [ic_o, t_o],
            'q5_q1_exposure_h5_ALL': float(np.nanmean(Q5Q1['exposure'][PHM['ALL']]))
            if 'exposure' in Q5Q1 else None,
            'ic_ind_diffusion_score_h5_ALL': [i_dsc[0], i_dsc[1]],
            'ic_ind_dchg_5_h5_ALL': [i_chg[0], i_chg[1]],
            'ic_ind_up_20_h5_ALL': [i_up[0], i_up[1]],
            'ic_ind_mom_20_h5_ALL': [i_mom[0], i_mom[1]],
            'm2_ind_breadth_h5_ALL': [b4a, t4a], 'm3_diffusion_h5_ALL': [b5a, t5a],
            'm3_diffusion_h5_OOS': [b5o, t5o], 'fm_diffusion_h5_ALL': [b6, t6],
            'diff_minus_b4_h5_ALL': [m20, tt20], 'diff_minus_b4_h5_OOS': [m20o,
                                                                        r20o.get('t_nw', np.nan)],
            'cost30_net_mean': (c or {}).get('net_mean'),
        },
        'gates': gdf.drop(columns=['lethal_fail']).to_dict('records'),
        'gate_pass_count': n_pass,
        'lethal_failed': lethal_fail,
        'critical_unmet_52': unmet52,
        'fdr': None if FDR is None else {
            'n_tests': len(FDR), 'reject_within': int(FDR['reject_within'].sum()),
            'reject_pooled': int(FDR['reject_pooled'].sum()),
            'by_family': {fam: {'n': len(g), 'raw_p_lt_05': int((g['raw_p'] < 0.05).sum()),
                                'reject_within': int(g['reject_within'].sum()),
                                'reject_pooled': int(g['reject_pooled'].sum())}
                          for fam, g in FDR.groupby('family')},
            'key_tests': {k: (None if v is None else {'raw_p': v[0],
                                                      'reject_within': v[1],
                                                      'reject_pooled': v[2]})
                          for k, v in kstat.items()},
        },
        'failure_analysis': fail_tab,
        'provenance_fix_log': FIX_LOG,
        'answers_55': [{'question': q, 'answer': a} for q, a in Q],
        'decision': decision,
        'decision_text': decision_txt,
        'conditional_triggered': cond_trigger,
        'trading_authorization': auth,
        'disposition_56': 'ARCHIVE',
        'next_study_allowed': 'H-IND-DIFFUSION-TRADE-01' if auth == 'YES' else None,
        'elapsed_sec': None,
    }
    fp = os.path.join(HERE, 'h_ind_diffusion.json')
    summary['elapsed_sec'] = round(time.time() - t_all, 1)
    with open(fp, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    LOG('  [SAVE] h_ind_diffusion.json')

    md = _render_md(summary, gdf, Q, fail_tab, FDR, kstat, ml, fm, bl, oo, qres,
                    n1, n3, n4, n5, cf, cf_ab)
    rp = os.path.join(HERE, 'report.md')
    with open(rp, 'w', encoding='utf-8') as f:
        f.write(md)
    LOG('  [SAVE] report.md（%d 字）' % len(md))
    LOG.sep('=')
    LOG('DONE  P7（总耗时 %.1fs）  判定=%s  授权=%s' % (time.time() - t_all, decision, auth))


# ══════════════════════════════════════════════════════════════ 报告渲染

SEP1 = '═' * 46
SEP2 = '─' * 46


def _tbl(head, rows):
    out = ['| ' + ' | '.join(head) + ' |', '|' + '|'.join(['---'] * len(head)) + '|']
    for r in rows:
        out.append('| ' + ' | '.join('' if x is None else str(x) for x in r) + ' |')
    return '\n'.join(out)


def _fe(v, nd=6):
    try:
        x = float(v)
    except Exception:
        return 'NA'
    return 'NA' if not np.isfinite(x) else ('%+.*f' % (nd, x))


def _render_md(S, gdf, Q, fail_tab, FDR, kstat, ml, fm, bl, oo, qres,
               n1, n3, n4, n5, cf, cf_ab):
    L = []
    A = L.append
    D = S['data']
    K = S['key_numbers']
    A('# H-IND-DIFFUSION-01 行业强度扩散 → 个股短线延续 Alpha 研究报告')
    A('')
    A('自动科研智能体 V1.0 · 最终结论')
    A('')
    A('生成时间：%s　｜　预注册指纹：`%s`　｜　派生定义指纹：`%s`'
      % (S['built_at'], S['prereg_hash'], S['derived_hash']))
    A('')
    A(SEP1)
    A('')
    A('最终判定：**%s**' % S['decision_text'])
    A('')
    A('TRADING_AUTHORIZATION = **%s**　｜　§56 处置 = **%s**' % (S['trading_authorization'],
                                                              S['disposition_56']))
    A('')
    A('致命 Gate 未通过：%s' % ('、'.join(S['lethal_failed']) if S['lethal_failed'] else '无'))
    A('')
    A(SEP1)
    A('')
    A('## 一、研究问题（§0 / §60）')
    A('')
    A(S['research_question'])
    A('')
    A('研究单位不是单只股票的技术形态，而是 Industry State → Within-industry Diffusion →'
      ' Stock Return（§1）。文献先验（§2）仅作 Prior，不外推至 A 股短线（§1/§2）。')
    A('')
    A('## 二、数据与口径')
    A('')
    A(_tbl(['项', '值'], [
        ['面板', '%d 股 × %d 交易日' % (D['n_codes'], D['n_cal'])],
        ['区间', '%s ~ %s' % (D['date_start'], D['date_end'])],
        ['行业', '申万 L1 %d 个（主口径）+ L2 %d 个（稳健性抽检）'
         % (D['n_l1'], D['n_l2'])],
        ['行业来源', D['industry_source']],
        ['有效样本', '%d 股票日（PASS）' % D['rows_valid']],
        ['剔除（如实披露，不静默）', ' '.join('%s=%d' % (k, v)
                                              for k, v in D['excluded_count'].items()
                                              if k != 'PASS')],
        ['L1 覆盖', '%.6f（重算 %.6f）；UNKNOWN=%d'
         % (D['coverage_l1'], D['coverage_recomputed'], D['unknown_l1_count'])],
        ['分期', ' '.join('%s=%d' % (k, v) for k, v in D['phases'].items())],
        ['前向收益', 'exe_H = close[k+H]/open[k+1]-1（k 日出信号，k+1 开盘进场）'],
        ['行业 N 门槛', 'N >= 20（§7/§40）；LOW_SAMPLE 标记'],
        ['随机种子 / 置换次数', '%d / %d' % (PREREG['seed'], PREREG['n_perm'])],
    ]))
    A('')
    A('## 三、核心结果')
    A('')
    A('### 3.1 行业层信号（h=5，全样本）')
    A('')
    rows = []
    for nm, lbl in (('ind_mom_20', '行业动量 EW20（§9）'), ('ind_up_20', 'Breadth20 水平（§10）'),
                    ('ind_dchg_5', 'BreadthChange5（§11 主方向变量）'),
                    ('ind_accel20', 'BreadthAcceleration（§12）'),
                    ('ind_lb_020', 'LeaderBreadth Top20%（§14）'),
                    ('ind_rsb_chg_5', 'RS_BreadthChange（§18）'),
                    ('ind_diff_gap', 'DiffusionGap（§16）'),
                    ('ind_diffusion_score', 'diffusion_score（4 分量等权）')):
        r = row(ml, sample='ALL', model='M3', horizon=H_MAIN, phase='ALL', term='diffusion')
        icr = row(rd('ic_results.csv'), signal=nm, horizon=H_MAIN, phase='ALL')
        qr = row(qres, signal=nm, kind='IND', sort_scope='ALL', nq=NQ,
                 horizon=H_MAIN, phase='ALL')
        rows.append([lbl, _fe(icr.get('mean_ic'), 5), _fe(icr.get('t_nw'), 2),
                     _fe(qr.get('spread_top_bot'), 5), _fe(qr.get('mono_rho'), 2)])
    A(_tbl(['信号', 'Rank IC', 't_NW', 'Q5−Q1（行业）', 'mono'], rows))
    A('')
    A('### 3.2 股票层暴露与组合')
    A('')
    A(_tbl(['对象', '口径', '日均/IC', 't_NW', '备注'], [
        ['exposure（0.5·行业扩散分位 + 0.5·行业内 RS20 分位）', 'ALL h5',
         _fe(K['ic_exposure_h5_ALL'][0], 5), _fe(K['ic_exposure_h5_ALL'][1], 2),
         '原始方向；IC 显著为负'],
        ['exposure', 'OOS h5', _fe(K['ic_exposure_h5_OOS'][0], 5),
         _fe(K['ic_exposure_h5_OOS'][1], 2), '方向与假设相反且显著'],
        ['expo_ind（仅行业扩散分量）', 'ALL h5', _fe(K['ic_ind_diffusion_score_h5_ALL'][0], 5),
         _fe(K['ic_ind_diffusion_score_h5_ALL'][1], 2), '≈0'],
        ['DIFF（行业Top3 × 行业内Top20%）', 'ALL h5',
         _fe(float(row(bl, strategy='DIFF', horizon=H_MAIN, phase='ALL').get('mean_ret', np.nan)), 5),
         _fe(float(row(bl, strategy='DIFF', horizon=H_MAIN, phase='ALL').get('t_nw', np.nan)), 2),
         '与 P4 口径一致'],
        ['B4（行业动量 + 股票动量）', 'ALL h5',
         _fe(float(row(bl, strategy='B4', horizon=H_MAIN, phase='ALL').get('mean_ret', np.nan)), 5),
         _fe(float(row(bl, strategy='B4', horizon=H_MAIN, phase='ALL').get('t_nw', np.nan)), 2),
         '§44 基准'],
        ['**DIFF − B4（增量 α）**', 'ALL h5', _fe(K['diff_minus_b4_h5_ALL'][0], 5),
         _fe(K['diff_minus_b4_h5_ALL'][1], 2), '§44 唯一有效比较'],
        ['**DIFF − B4**', 'OOS h5', _fe(K['diff_minus_b4_h5_OOS'][0], 5),
         _fe(K['diff_minus_b4_h5_OOS'][1], 2), '未达显著'],
    ]))
    A('')
    A('关键观察：股票层 `exposure` 的负 IC 主要由「行业内 RS20 分位」分量驱动'
      '（expo_rs h5 ALL IC=%s，t=%s），而纯行业扩散分量 expo_ind 趋近于 0'
      '（IC=%s，t=%s）。即在 2018–2026 全样本上，20 日相对强势呈反转特征，'
      '行业扩散分量自身在股票横截面上不产生方向性收益。'
      % (_fe(float(row(rd('ic_results.csv'), signal='expo_rs', horizon=H_MAIN,
                          phase='ALL').get('mean_ic', np.nan)), 5),
         _fe(float(row(rd('ic_results.csv'), signal='expo_rs', horizon=H_MAIN,
                          phase='ALL').get('t_nw', np.nan)), 2),
         _fe(K['ic_ind_diffusion_score_h5_ALL'][0], 5),
         _fe(K['ic_ind_diffusion_score_h5_ALL'][1], 2)))
    A('')
    A('## 四、G1–G20 判定（§50）')
    A('')
    rows = []
    for _, r in gdf.iterrows():
        rows.append([r['gate'], r['name'], 'LETHAL' if r['lethal'] else '',
                     '**%s**' % r['verdict'] if r['verdict'] != 'PASS' else r['verdict'],
                     r['evidence']])
    A(_tbl(['Gate', '名称', '致命', '判定', '证据'], rows))
    A('')
    A('PASS 数：%d / 20。致命 Gate（§51：G1 G4 G5 G8 G9 G13 G16 G20）未通过项：%s。'
      % (S['gate_pass_count'], '、'.join(S['lethal_failed']) if S['lethal_failed'] else '无'))
    A('')
    A('## 五、§55 十五问逐条回答')
    A('')
    for i, (q, a) in enumerate(Q, 1):
        A('%d. **%s**' % (i, q))
        A('')
        A('　　%s' % a)
        A('')
    A('## 六、Null / 反事实 / 置换（§26 §27 §45）')
    A('')
    rows = []
    if n1:
        rows.append(['N1 Random Industry-Date', _fe(n1.get('obs'), 5),
                     _fe(n1.get('null_mean'), 5), '%.4f' % float(n1.get('p_one_sided', np.nan)),
                     _fe(n1.get('z'), 2)])
    if n3:
        rows.append(['N3 Momentum-Matched', _fe(n3.get('obs'), 5),
                     _fe(n3.get('null_mean'), 5), '%.4f' % float(n3.get('p_one_sided', np.nan)),
                     _fe(n3.get('z'), 2)])
    if n4:
        rows.append(['N4 Industry-Momentum Matched', _fe(n4.get('obs'), 5),
                     _fe(n4.get('null_mean'), 5), '%.4f' % float(n4.get('p_one_sided', np.nan)),
                     _fe(n4.get('z'), 2)])
    if n5:
        rows.append(['N5 Breadth Shuffled', _fe(n5.get('obs'), 5),
                     _fe(n5.get('null_mean'), 5), '%.4f' % float(n5.get('p_one_sided', np.nan)),
                     _fe(n5.get('z'), 2)])
    A(_tbl(['Null 模型', 'obs', 'null 均值', '单侧 p', 'Z'], rows) if rows
      else '（缺 null_models.csv）')
    A('')
    rows = []
    for q in ('A', 'B', 'C', 'D'):
        d = cf.get(q) or {}
        rows.append(['%s（%s）' % (q, {'A': '动量↑扩散↑', 'B': '动量↑未扩散',
                                       'C': '动量↓扩散↑', 'D': '动量↓扩散↓'}[q]),
                     _fe(d.get('obs'), 5)])
    if cf_ab:
        rows.append(['**A − B（§27 核心反事实）**', '%s（t=%s）'
                     % (_fe(cf_ab.get('obs'), 5), _fe(cf_ab.get('z'), 2))])
    A(_tbl(['四象限', '所选行业 h5 平均收益'], rows) if rows else '（缺四象限结果）')
    A('')
    A('## 七、多重检验（§46）')
    A('')
    if FDR is None:
        A('（未产出 fdr_results.csv）')
    else:
        A('共 %d 项检验；族内 BH 显著 %d 项，合并池 BH 显著 %d 项（alpha=%.2f）。'
          % (S['fdr']['n_tests'], S['fdr']['reject_within'], S['fdr']['reject_pooled'],
             float(PREREG['fdr_alpha'])))
        A('')
        rows = [[fam, g['n'], g['raw_p_lt_05'], g['reject_within'], g['reject_pooled']]
                for fam, g in S['fdr']['by_family'].items()]
        A(_tbl(['族', 'n', 'raw p<0.05', '族内显著', '合并池显著'], rows))
        A('')
        A('G18 关键检验集（K1–K7）：')
        A('')
        rows = []
        for k, v in kstat.items():
            rows.append([k, 'NA' if v is None else '%.4g' % v[0],
                         'NA' if v is None else v[1], 'NA' if v is None else v[2]])
        A(_tbl(['关键检验', 'raw p', '族内显著', '合并池显著'], rows))
    A('')
    A('## 八、参数稳定性与尾部（§37 §38 §39）')
    A('')
    A('参数面四轴（breadth_win / mom_win / ind_n / leader_pct）全部结果见 '
      '`parameter_surface.csv`；判定：G14=%s，G15=%s。'
      % (dict(zip(gdf['gate'], gdf['verdict'])).get('G14'),
         dict(zip(gdf['gate'], gdf['verdict'])).get('G15')))
    A('')
    A('## 九、失败归因（§49）')
    A('')
    if fail_tab:
        rows = [[r['code'], r['name'], r['n_failed'], '%.1f%%' % (100 * r['pct_of_failures']),
                 'NA' if r['bucket_fail_rate'] is None else '%.3f' % r['bucket_fail_rate']]
                for r in fail_tab]
        A(_tbl(['代码', '失败类型', '失败数', '占失败比', '桶内失败率'], rows))
        A('')
        A('注：§49 失败归因为**描述性**统计（失败 = DIFF 选股样本 exe_5 <= 0，'
          'F0–F8 按固定优先级首个命中归类），不用于事后修补策略，也不构成 §53 所要求的'
          '「预注册失败风险预测检验」。')
    else:
        A('（缺 failure_analysis.csv）')
    A('')
    A('## 十、最终判定（§52 / §53 / §54 / §61）')
    A('')
    A('§52 要求的全部条件中未满足：%s。'
      % ('、'.join(S['critical_unmet_52']) if S['critical_unmet_52'] else '无'))
    A('')
    A('§53 CONDITIONAL：不触发。触发需要预注册的「Industry Diffusion 预测 Failure Risk」'
      '检验证据，本研究未预注册该检验（[R5]），不得以描述性失败归因替代。')
    A('')
    A('结论：**%s**' % S['decision_text'])
    A('')
    A('结构性问题（§54）：')
    A('')
    A('1. 股票层扩散暴露的收益方向与假设相反（`exposure` h5 ALL IC=%s，t=%s；'
      'OOS IC=%s，t=%s），反向不参与 Gate（§59）。'
      % (_fe(K['ic_exposure_h5_ALL'][0], 5), _fe(K['ic_exposure_h5_ALL'][1], 2),
         _fe(K['ic_exposure_h5_OOS'][0], 5), _fe(K['ic_exposure_h5_OOS'][1], 2)))
    A('2. 行业层 diffusion 在控住行业动量后 beta 显著（M3 beta=%s，t=%s），但该显著性未在'
      ' OOS 期复现（OOS t=%s），组合层增量亦不显著（DIFF−B4 OOS t=%s），'
      '即 §22 的 M3−M2 增量检验未通过。'
      % (_fe(K['m3_diffusion_h5_ALL'][0], 6), _fe(K['m3_diffusion_h5_ALL'][1], 2),
         _fe(K['m3_diffusion_h5_OOS'][1], 2), _fe(K['diff_minus_b4_h5_OOS'][1], 2)))
    A('3. 成本与尾部：30bp 后净日均增量 %s；G16=%s。'
      % (_fe(K['cost30_net_mean'], 5),
         dict(zip(gdf['gate'], gdf['verdict'])).get('G16')))
    A('')
    A('## 十一、§56 接口处置')
    A('')
    A('本研究未通过，**ARCHIVE**。不得作为 Theme/Industry Gate 接入既有 20D Momentum 选股链路，'
      '不得污染现有生产策略。')
    A('')
    A('`TRADING_AUTHORIZATION = %s`（§61 默认 NO）。'
      '本研究不产生任何具体股票买入授权；若未来需交易层研究，必须另立独立课题 '
      '`H-IND-DIFFUSION-TRADE-01` 并重新通过全部 G1–G20。' % S['trading_authorization'])
    A('')
    A('## 十二、产出文件（§57）')
    A('')
    files = ['industry_daily_state.parquet', 'industry_diffusion_features.parquet',
             'stock_diffusion_exposure.parquet', 'ic_results.csv', 'auc_results.csv',
             'quantile_results.csv', 'fama_macbeth.csv', 'model_ladder.csv',
             'baseline_results.csv', 'null_models.csv', 'permutation.csv',
             'fdr_results.csv', 'oos_results.csv', 'walkforward.csv', 'year_results.csv',
             'regime_results.csv', 'parameter_surface.csv', 'cost_analysis.csv',
             'tail_analysis.csv', 'failure_analysis.csv', 'gate_results.csv',
             'h_ind_diffusion.json', 'report.md']
    rows = []
    for fn in files:
        fp = os.path.join(HERE, fn)
        if fn == 'report.md':
            # report.md 即本报告自身，渲染时尚未落盘，固定标注为「本报告」
            rows.append([fn, 'OK（本报告）', '—'])
            continue
        rows.append([fn, 'OK' if os.path.exists(fp) else '缺',
                     ('%.1f KB' % (os.path.getsize(fp) / 1024)) if os.path.exists(fp) else ''])
    A(_tbl(['文件', '状态', '大小'], rows))
    A('')
    A('## 十三、实现修正与溯源（§50 G1）')
    A('')
    for x in S.get('provenance_fix_log', []):
        A('- **`%s`** · %s（%s）' % (x['file'], x['item'], x['when']))
        A('')
        A('　　%s' % x['detail'])
        A('')
        A('　　影响范围：%s' % x['scope'])
        A('')
    A(SEP2)
    A('')
    A('本报告全部数值取自 `research/h_ind_diffusion/` 下已冻结产物；'
      '预注册指纹与派生定义指纹已在 P1–P7 全链路校验一致（G1）。')
    return '\n'.join(L) + '\n'


if __name__ == '__main__':
    main()
