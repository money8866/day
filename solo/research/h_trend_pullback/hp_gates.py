# -*- coding: utf-8 -*-
"""
H-TREND-PULLBACK-01  P3：反事实与门控（§26 动量控制 / §27 行业控制 / §29 随机回撤 Null）

产出
  A) §26 动量控制
       A1 动量匹配反事实 B4_MomentumMatched（同日、最近邻 mom20+mom60）→ momentum-adjusted alpha
       A2 动量中性化后的增量 IC（对 rank(mom20) rank(mom60) 求偏相关）
  B) §27 行业控制
       B1 行业×动量匹配反事实 B5_IndustryMomentumMatched（同日同 SW_L1、最近邻 mom20+total_mv+rv_20）
       B2 行业中性化后的增量 IC（日内分行业内去均值）
  C) §29 随机回撤 Null：B3_TrendRandomPullback（核心反事实，§30 core_counterfactual）
  D) 基线对照：B1_AllStocks / B2_StrongTrend
  E) 门控判定（仅 §26 / §27 / §29 三项；G 编号映射见运行日志声明）

纪律
  · 只做推断与对照，不调参、不改阈值、不预设方向（§3）
  · 匹配用「同日最近邻」，不做跨日池化；反事实池显式剔除真实 E1 信号日本身
  · 单侧经验 p 与双侧经验 p 同时报告，不择优
"""
import os
import sys
import time
import warnings

import numpy as np
import pandas as pd

from hp_common import (DATA, PAN, OUTD, PREREG, mklog, pload_meta, pload_day,
                       ic_stats)

warnings.filterwarnings('ignore', category=RuntimeWarning)
LOG = mklog('gates')

HZ = tuple(PREREG['primary_horizons'])
H_MAIN = 5
PHASES = ('TRAIN', 'VALID', 'OOS', 'LIVE-LIKE')
PHASE_ID = {n: i + 1 for i, n in enumerate(PHASES)}
SEED = int(PREREG['seed'])
NSIM = int(PREREG['n_sim'])
LAG_NW = 19

MATCH_KEYS_MOM = ('mom20', 'mom60')
MATCH_KEYS_IND = ('mom20', 'total_mv', 'rv_20')

# 中性化增量 IC 只对「回撤质量」类特征做（§26/§27 问的是回撤特征是否只是动量的代理）
NEUT_FEATS = ['depth', 'speed', 'ep_days', 'since_high', 'since_pb',
              'cl_ma20', 'lo_ma20', 'vol_ratio', 'to_ratio', 'atr_ratio',
              'rv_ratio', 'dn_vol_ratio', 'dn_day_ratio', 'rs_pb_mkt', 'rs_pb_ind']


# ══════════════════════════════════════════════════ 通用工具

def _z(df, cols):
    """按列做全样本 z 标准化（NaN 保持 NaN）"""
    Z = np.empty((len(df), len(cols)), dtype=np.float64)
    for i, c in enumerate(cols):
        v = df[c].values.astype(np.float64)
        m = np.isfinite(v)
        if m.sum() < 2:
            Z[:, i] = np.nan
            continue
        mu, sd = v[m].mean(), v[m].std(ddof=1)
        Z[:, i] = np.where(m, (v - mu) / sd if sd > 0 else 0.0, np.nan)
    return Z


def _ranks(A):
    """逐日（axis=1）平均秩"""
    from scipy.stats import rankdata
    return rankdata(A, axis=1, method='average', nan_policy='omit')


def _to_panel(TD, col, NCODES, NCAL):
    P = np.full((NCAL, NCODES), np.nan, dtype=np.float32)
    v = TD[col].values.astype(np.float64)
    ok = np.isfinite(v)
    P[TD['k'].values[ok], TD['code'].values[ok]] = v[ok]
    return P


def _daily_t(series_by_day):
    """日度序列 -> NW t"""
    s = np.asarray(series_by_day, dtype=np.float64)
    s = s[np.isfinite(s)]
    if len(s) < 5:
        return np.nan, len(s)
    return ic_stats(s, lag=LAG_NW)['t_nw'], len(s)


# ══════════════════════════════════════════════════ A/B 匹配反事实

def matched_counterfactual(E, TD, keys, ind_matched, h, tag):
    """同日最近邻匹配反事实。

    对每个 E1 事件，在「同日强趋势日且非 E1 信号日」池中，取 keys 维度欧氏距离最近的样本，
    用其 exe_h 作为反事实收益；返回 (adj_alpha, t_nw, n_used, matched_exe)。
    """
    ycol = 'exe_%d' % h
    NCAL = int(pload_meta()['NCAL'])
    TD = TD.reset_index(drop=True)
    ekey = E['code'].values.astype(np.int64) * NCAL + E['k'].values.astype(np.int64)
    tkey = TD['code'].values.astype(np.int64) * NCAL + TD['k'].values.astype(np.int64)
    is_ev = np.isin(tkey, ekey)
    Ztd = _z(TD, keys)
    ok_z = np.isfinite(Ztd).all(1)

    ev_idx = np.arange(len(E))
    m_ev = np.isfinite(E[ycol].values)
    # 事件端的 z 用 TD 的同一把尺子（同均值/同标准差），保证与池子可比
    Zev = np.empty((len(E), len(keys)), dtype=np.float64)
    for i, c in enumerate(keys):
        v = TD[c].values.astype(np.float64)
        f = np.isfinite(v)
        mu, sd = v[f].mean(), v[f].std(ddof=1)
        ve = E[c].values.astype(np.float64)
        Zev[:, i] = np.where(np.isfinite(ve) & (sd > 0), (ve - mu) / sd, np.nan)
    m_ev &= np.isfinite(Zev).all(1)

    pool = np.where((~is_ev) & ok_z & np.isfinite(TD[ycol].values))[0]
    pdate = TD['date'].values[pool]
    pind = TD['ind_l1'].values[pool]
    pz = Ztd[pool]
    pex = TD[ycol].values[pool].astype(np.float64)

    edate = E['date'].values
    eind = E['ind_l1'].values

    matched = np.full(len(E), np.nan)
    # 按日（行业匹配时再按行业）分桶
    if ind_matched:
        bkey = pdate.astype(np.int64) * 100000 + pind.astype(np.int64)
        order = np.argsort(bkey, kind='stable')
        pkey_s = bkey[order]
        uk, ustart = np.unique(pkey_s, return_index=True)
        ucnt = np.diff(np.append(ustart, len(pkey_s)))
        kmap = {int(k): i for i, k in enumerate(uk)}
        ekeyb = edate.astype(np.int64) * 100000 + eind.astype(np.int64)
        # 事件 -> 桶
        e_of_bucket = {}
        for i in ev_idx[m_ev]:
            b = int(ekeyb[i])
            e_of_bucket.setdefault(b, []).append(i)
        for b, eis in e_of_bucket.items():
            j = kmap.get(b)
            if j is None:
                continue
            sl = order[ustart[j]:ustart[j] + ucnt[j]]
            A = pz[sl]
            B = Zev[np.asarray(eis)]
            d2 = ((B[:, None, :] - A[None, :, :]) ** 2).sum(2)
            jj = d2.argmin(1)
            matched[np.asarray(eis)] = pex[sl][jj]
    else:
        order = np.argsort(pdate, kind='stable')
        pdate_s = pdate[order]
        ud, ustart = np.unique(pdate_s, return_index=True)
        ucnt = np.diff(np.append(ustart, len(pdate_s)))
        kmap = {int(k): i for i, k in enumerate(ud)}
        e_of_date = {}
        for i in ev_idx[m_ev]:
            e_of_date.setdefault(int(edate[i]), []).append(i)
        for d, eis in e_of_date.items():
            j = kmap.get(d)
            if j is None:
                continue
            sl = order[ustart[j]:ustart[j] + ucnt[j]]
            A = pz[sl]
            B = Zev[np.asarray(eis)]
            d2 = ((B[:, None, :] - A[None, :, :]) ** 2).sum(2)
            jj = d2.argmin(1)
            matched[np.asarray(eis)] = pex[sl][jj]

    use = m_ev & np.isfinite(matched)
    diff = E[ycol].values[use] - matched[use]
    dts = E['date'].values[use]
    dm = pd.Series(diff).groupby(dts).mean().values
    t, nd = _daily_t(dm)
    LOG('   [%s] n=%d  日=%d  adj_alpha %+.4f  t %+.2f  (E1 %.4f - matched %.4f)'
        % (tag, int(use.sum()), nd, float(diff.mean()), t,
           float(E[ycol].values[use].mean()), float(matched[use].mean())))
    return {'tag': tag, 'h': h, 'n': int(use.sum()), 'n_day': nd,
            'adj_alpha': float(diff.mean()), 't_nw': t,
            'e1_mean': float(E[ycol].values[use].mean()),
            'matched_mean': float(matched[use].mean())}, matched, use


# ══════════════════════════════════════════════════ 中性化增量 IC

def _partial_mom(x, y, c1, c2):
    """对 [1, c1, c2] 残差化后求相关系数（正规方程闭式解）"""
    n = len(x)
    X = np.column_stack([np.ones(n), c1, c2])
    G = X.T @ X
    try:
        bx = np.linalg.solve(G, X.T @ x)
        by = np.linalg.solve(G, X.T @ y)
    except np.linalg.LinAlgError:
        return np.nan
    rx = x - X @ bx
    ry = y - X @ by
    dx = float((rx * rx).sum())
    dy = float((ry * ry).sum())
    if dx <= 0 or dy <= 0:
        return np.nan
    return float((rx * ry).sum() / np.sqrt(dx * dy))


def _partial_ind(x, y, g):
    """日内按行业去均值后求相关系数"""
    rx = x.copy()
    ry = y.copy()
    gu = np.unique(g)
    for gg in gu:
        m = (g == gg)
        if m.sum() >= 2:
            rx[m] -= rx[m].mean()
            ry[m] -= ry[m].mean()
    dx = float((rx * rx).sum())
    dy = float((ry * ry).sum())
    if dx <= 0 or dy <= 0:
        return np.nan
    return float((rx * ry).sum() / np.sqrt(dx * dy))


def neutral_ic(TD, NCODES, NCAL, mode, h):
    """回撤质量特征在控制动量 / 行业后的增量 IC。返回 DataFrame"""
    ycol = 'exe_%d' % h
    pb = TD['in_ep'].values == 1
    TDp = TD[pb]
    T = _to_panel(TDp, ycol, NCODES, NCAL)
    M1 = _to_panel(TDp, 'mom20', NCODES, NCAL)
    M2 = _to_panel(TDp, 'mom60', NCODES, NCAL)
    ID = _to_panel(TDp, 'ind_l1', NCODES, NCAL)
    RT = _ranks(T)
    RM1 = _ranks(np.where(np.isfinite(M1), M1, np.nan))
    RM2 = _ranks(np.where(np.isfinite(M2), M2, np.nan))
    rows = []
    for c_ in NEUT_FEATS:
        S = _to_panel(TDp, c_, NCODES, NCAL)
        VAL = np.isfinite(S) & np.isfinite(T)
        RS = _ranks(np.where(VAL, S, np.nan))
        ic = np.full(NCAL, np.nan)
        for k in range(NCAL):
            m = VAL[k]
            if m.sum() < 30:
                continue
            f = np.where(m)[0]
            x = RS[k, f]
            y = RT[k, f]
            if mode == 'mom':
                c1 = RM1[k, f]
                c2 = RM2[k, f]
                good = np.isfinite(x) & np.isfinite(y) & np.isfinite(c1) & np.isfinite(c2)
                if good.sum() < 30:
                    continue
                ic[k] = _partial_mom(x[good], y[good], c1[good], c2[good])
            else:
                g = ID[k, f]
                good = np.isfinite(x) & np.isfinite(y) & np.isfinite(g)
                if good.sum() < 30:
                    continue
                ic[k] = _partial_ind(x[good], y[good], g[good])
        st = ic_stats(ic, name='%s_%s_h%d' % (mode, c_, h))
        rows.append({'mode': mode, 'feature': c_, 'horizon': h,
                     'ic_neutral': st['mean'], 't_nw': st['t_nw'], 'n_day': st['n']})
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════ C 随机回撤 Null

def random_pullback_null(E, TD, h, rng):
    """B3_TrendRandomPullback：每个趋势 leg 内随机取一天（剔除真实 E1 信号日）作为伪信号。"""
    ycol = 'exe_%d' % h
    NCAL = int(pload_meta()['NCAL'])
    ekey = E['code'].values.astype(np.int64) * NCAL + E['k'].values.astype(np.int64)
    tkey = TD['code'].values.astype(np.int64) * NCAL + TD['k'].values.astype(np.int64)
    keep = (~np.isin(tkey, ekey)) & np.isfinite(TD[ycol].values)
    # TD['leg'] 为「code 内」局部编号（hp_events 的 cumsum 逐 code 重置），
    # 必须用 (code, leg) 复合键分组，否则不同 code 的同号 leg 会被合并成一组。
    BIG = np.int64(NCAL) + 2
    code_v = TD['code'].values.astype(np.int64)
    leg_v = TD['leg'].values.astype(np.int64)
    legkey = code_v * BIG + leg_v
    pos = leg_v >= 0
    order = np.argsort(legkey, kind='stable')
    l_s = legkey[order]
    y_s = TD[ycol].values[order].astype(np.float64)
    k_s = keep[order] & pos[order]
    uk, ustart = np.unique(l_s, return_index=True)
    ucnt = np.diff(np.append(ustart, len(l_s)))

    # 只用「产出过 E1」的 leg（同样用复合键）
    e_legs = np.unique(E['code'].values.astype(np.int64) * BIG
                       + E['leg'].values.astype(np.int64))
    li = np.searchsorted(uk, e_legs)
    li = li[(li < len(uk)) & (uk[np.minimum(li, len(uk) - 1)] == e_legs)]
    if len(li) == 0:
        return None
    st = ustart[li]
    cn = ucnt[li]

    # 每个 leg 内「非 E1」子集的可用位置
    valid_pos = []
    valid_leg = []
    for j, (a, b) in enumerate(zip(st, cn)):
        idx = np.arange(a, a + b)
        ok = k_s[idx]
        if ok.any():
            valid_pos.append(idx[ok])
            valid_leg.append(j)
    if not valid_pos:
        return None
    allpos = np.concatenate(valid_pos)
    allleg = np.concatenate([np.full(len(v), i, dtype=np.int64)
                             for i, v in enumerate(valid_pos)])
    nleg = len(valid_pos)
    # 每 leg 的候选区间
    o = np.argsort(allleg, kind='stable')
    allpos, allleg = allpos[o], allleg[o]
    lstart = np.searchsorted(allleg, np.arange(nleg), side='left')
    lcnt = np.searchsorted(allleg, np.arange(nleg), side='right') - lstart

    real = float(np.nanmean(E[ycol].values))
    null_means = np.empty(NSIM, dtype=np.float64)
    step = max(1, 100)
    for s0 in range(0, NSIM, step):
        s1 = min(NSIM, s0 + step)
        u = rng.random((s1 - s0, nleg))
        pick = lstart[None, :] + (u * lcnt[None, :]).astype(np.int64)
        pick = np.minimum(pick, lstart[None, :] + lcnt[None, :] - 1)
        vals = y_s[allpos[pick]]
        null_means[s0:s1] = np.nanmean(vals, 1)
    ge = int((null_means >= real).sum())
    le = int((null_means <= real).sum())
    p_hi = (1.0 + ge) / (1.0 + NSIM)
    p_lo = (1.0 + le) / (1.0 + NSIM)
    LOG('   [B3_TrendRandomPullback h=%d] leg=%d  真实 E1 mean %+.4f   '
        'Null mean %+.4f (sd %.4f, p05 %+.4f, p95 %+.4f)'
        % (h, nleg, real, float(null_means.mean()), float(null_means.std(ddof=1)),
           float(np.quantile(null_means, 0.05)), float(np.quantile(null_means, 0.95))))
    LOG('      经验 p：真实>=Null %.4f ；真实<=Null %.4f' % (p_hi, p_lo))
    return {'h': h, 'n_leg': int(nleg), 'real_mean': real,
            'null_mean': float(null_means.mean()), 'null_sd': float(null_means.std(ddof=1)),
            'null_p05': float(np.quantile(null_means, 0.05)),
            'null_p95': float(np.quantile(null_means, 0.95)),
            'p_real_ge_null': p_hi, 'p_real_le_null': p_lo}


# ══════════════════════════════════════════════════ D 基线

def all_stocks_baseline(h):
    """B1_AllStocks：全有效域 stock-day 的 exe_h 均值（由面板直接算）

    exe_h(k) = close[k+h] / open[k+1] - 1；要求 k / k+1 / k+h 三日均在有效域内。
    """
    cl = np.load(os.path.join(PAN, 'close.npy')).astype(np.float64)
    op = np.load(os.path.join(PAN, 'open.npy')).astype(np.float64)
    v = np.load(os.path.join(PAN, 'valid.npy')).astype(bool)
    NCODES, NCAL = cl.shape
    L = NCAL - h - 1
    num = np.full((NCODES, NCAL), np.nan, dtype=np.float64)
    if L > 0:
        num[:, :L] = cl[:, h:h + L] / op[:, 1:L + 1] - 1.0
    ok = np.zeros((NCODES, NCAL), dtype=bool)
    if L > 0:
        ok[:, :L] = v[:, :L] & v[:, 1:L + 1] & v[:, h:h + L]
    val = num[ok]
    val = val[np.isfinite(val)]
    return float(val.mean()), int(len(val))


def strong_trend_baseline(TD, h):
    y = TD['exe_%d' % h].values.astype(np.float64)
    y = y[np.isfinite(y)]
    return float(y.mean()), int(len(y))


# ══════════════════════════════════════════════════ 主流程

def main():
    t0 = time.time()
    LOG.sep('=')
    LOG('H-TREND-PULLBACK-01  P3 反事实与门控  %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    LOG('口径：入场锚定 E1（Pullback Observation Entry），主视界 h=%d（次日开盘进场 exe_h）' % H_MAIN)
    LOG('声明：本脚本按 §26 / §27 / §29 产出证据；G 编号的权威定义以用户提交规格 §51/§52 为准，')
    LOG('      本脚本只对「动量控制 / 行业控制 / 随机回撤 Null」三项给出 PASS / FAIL 判定。')

    M = pload_meta()
    NCODES, NCAL = int(M['NCODES']), int(M['NCAL'])
    day = pload_day()
    TD = pd.read_parquet(os.path.join(DATA, 'hp_trenddays.parquet'))
    EV = pd.read_parquet(os.path.join(DATA, 'hp_events.parquet'))
    LOG('   trenddays %d 行 / E1 %d' % (len(TD), len(EV)))

    rng = np.random.default_rng(SEED)
    out_match, out_neut, out_null, out_base = [], [], [], []

    # ---------- D) 基线 ----------
    LOG.sep()
    LOG('D) 基线对照（h=%d）' % H_MAIN)
    b1, n1 = all_stocks_baseline(H_MAIN)
    b2, n2 = strong_trend_baseline(TD, H_MAIN)
    e1, n3 = strong_trend_baseline(EV, H_MAIN)
    LOG('   B1_AllStocks      n=%9d  mean %+.4f' % (n1, b1))
    LOG('   B2_StrongTrend    n=%9d  mean %+.4f' % (n2, b2))
    LOG('   E1_Signal         n=%9d  mean %+.4f' % (n3, e1))
    out_base.append({'baseline': 'B1_AllStocks', 'h': H_MAIN, 'n': n1, 'mean': b1})
    out_base.append({'baseline': 'B2_StrongTrend', 'h': H_MAIN, 'n': n2, 'mean': b2})
    out_base.append({'baseline': 'E1_Signal', 'h': H_MAIN, 'n': n3, 'mean': e1})

    # ---------- A) §26 动量控制 ----------
    LOG.sep()
    LOG('A) §26 动量控制：同日最近邻匹配（keys=%s），反事实池 = 同日强趋势日且非 E1' % (MATCH_KEYS_MOM,))
    for h in HZ:
        r, matched, use = matched_counterfactual(EV, TD, list(MATCH_KEYS_MOM), False, h,
                                                 'B4_MomentumMatched')
        out_match.append(dict(r, mode='mom'))
    LOG('   动量中性化增量 IC（偏相关；控制 rank(mom20) rank(mom60)）：')
    for h in HZ:
        dfn = neutral_ic(TD, NCODES, NCAL, 'mom', h)
        dfn['h'] = h
        out_neut.append(dfn)
        for _, r in dfn.iterrows():
            LOG('     %-12s h=%-2d  IC_partial %+.4f  t %+.2f  (nd=%d)'
                % (r['feature'], h, r['ic_neutral'], r['t_nw'], r['n_day']))

    # ---------- B) §27 行业控制 ----------
    LOG.sep()
    LOG('B) §27 行业控制')
    LOG('   B5_IndustryMomentumMatched：同日同 SW_L1 内最近邻（keys=%s）' % (MATCH_KEYS_IND,))
    for h in HZ:
        r, _, _ = matched_counterfactual(EV, TD, list(MATCH_KEYS_IND), True, h,
                                         'B5_IndustryMomentumMatched')
        out_match.append(dict(r, mode='ind'))
    LOG('   行业中性化增量 IC（日内分行业内去均值）：')
    for h in HZ:
        dfn = neutral_ic(TD, NCODES, NCAL, 'ind', h)
        dfn['h'] = h
        out_neut.append(dfn)
        for _, r in dfn.iterrows():
            LOG('     %-12s h=%-2d  IC_indneut %+.4f  t %+.2f  (nd=%d)'
                % (r['feature'], h, r['ic_neutral'], r['t_nw'], r['n_day']))

    # ---------- C) §29 随机回撤 Null ----------
    LOG.sep()
    LOG('C) §29 随机回撤 Null（B3_TrendRandomPullback，n_sim=%d，seed=%d）' % (NSIM, SEED))
    for h in HZ:
        r = random_pullback_null(EV, TD, h, rng)
        if r:
            out_null.append(r)

    # ---------- E) 门控判定 ----------
    LOG.sep()
    LOG('E) 门控判定（仅覆盖 §26 / §27 / §29；阈值取 PREREG 冻结值，不得事后调整）')
    MT = PREREG['momentum_threshold']
    PA = PREREG['perm_alpha']
    m5 = [r for r in out_match if r['mode'] == 'mom' and r['h'] == H_MAIN][0]
    i5 = [r for r in out_match if r['mode'] == 'ind' and r['h'] == H_MAIN][0]
    n5 = [r for r in out_null if r['h'] == H_MAIN][0]

    g_mom = 'PASS' if (m5['adj_alpha'] > MT and m5['t_nw'] > 0) else 'FAIL'
    g_ind = 'PASS' if (i5['adj_alpha'] > MT and i5['t_nw'] > 0) else 'FAIL'
    g_null = 'PASS' if (n5['real_mean'] > n5['null_mean'] and n5['p_real_ge_null'] < PA) else 'FAIL'
    LOG('   §26 动量控制  : adj_alpha %+.4f  t %+.2f  -> %s' % (m5['adj_alpha'], m5['t_nw'], g_mom))
    LOG('   §27 行业控制  : adj_alpha %+.4f  t %+.2f  -> %s' % (i5['adj_alpha'], i5['t_nw'], g_ind))
    LOG('   §29 随机回撤Null: real %+.4f vs null %+.4f  单侧 p %.4f  -> %s'
        % (n5['real_mean'], n5['null_mean'], n5['p_real_ge_null'], g_null))

    # ---------- 落盘 ----------
    pd.DataFrame(out_match).to_csv(os.path.join(OUTD, 'hp_gates_match.csv'), index=False)
    pd.concat(out_neut, ignore_index=True).to_csv(os.path.join(OUTD, 'hp_gates_neutral_ic.csv'),
                                                  index=False)
    pd.DataFrame(out_null).to_csv(os.path.join(OUTD, 'hp_gates_null.csv'), index=False)
    pd.DataFrame(out_base).to_csv(os.path.join(OUTD, 'hp_gates_baseline.csv'), index=False)
    LOG.sep()
    LOG('落盘: out/hp_gates_match.csv / hp_gates_neutral_ic.csv / hp_gates_null.csv / hp_gates_baseline.csv')
    LOG('P3 完成 %.0fs' % (time.time() - t0))
    LOG.sep('=')
    print('DONE')
    return 0


if __name__ == '__main__':
    sys.exit(main())
