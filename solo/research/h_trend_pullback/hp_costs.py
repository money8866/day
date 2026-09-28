# -*- coding: utf-8 -*-
"""
H-TREND-PULLBACK-01  P5：成本 / 尾部 / Null / 置换 / 多重检验

覆盖规格
  §44 成本敏感性（G14）          §46 尾部依赖与右尾贡献（G13）
  §48 Null 模型 N1 - N6          §49 置换检验（G15）
  §50 多重检验（BH-FDR，families = PREREG.fdr_families）

成本口径（预先声明，不以结果调整）
  · 一次完整往返 = 单边 bp × 2；进场 = open[k+1]，出场 = close[k+H]，即只发生一次买 + 一次卖
  · 逐日等权的「分数选择层」序列未做重叠持仓的资金占用建模，仅作经济意义量级参考（已在日志注明）

Null 模型（§48）
  N1_RandomStock             全有效域随机 stock-day（有放回，样本量 = E1 数量）
  N2_RandomDate              同一 code 的强趋势日中随机取日（剔除 E1 信号日本身）
  N3_RandomPullback          同一 trend leg 内随机取日（B3，与 P3 §29 同一估计量）
  N4_MomentumMatched         同日最近邻动量匹配反事实（B4）自助重抽样
  N5_IndustryMomentumMatched 同日同行业×动量匹配反事实（B5）自助重抽样
  N6_ShuffledPullbackQuality 回撤质量分数「日内打乱」（等价于联合打乱回撤质量特征的载荷分配）

置换检验（§49）
  · 主检验：模型分数 s_m1 在「日内」随机置换（保持当日样本集不变、只打乱分数与个股的对应）
  · 统计量：逐日横截面 IC（主）与 top-decile AUC（次）
  · N6 与主置换检验为同一估计量，二者共用同一次模拟（日志中显式声明，不重复计数）

纪律
  · 只做推断与稳健性检验：不调参、不改阈值、不预设方向（§3）
  · 阈值一律取 PREREG 冻结值；失败 Gate 不得救援（§26 / §52 / §55）
  · 经验 p 单侧 / 双侧全报，不择优；样本不足如实标注而非静默
"""
import os
import sys
import time
import warnings

import numpy as np
import pandas as pd
from scipy.stats import rankdata, norm

from hp_common import (DATA, PAN, OUTD, PREREG, mklog, pload_meta, pload_day,
                       ic_stats, bh_fdr)

warnings.filterwarnings('ignore', category=RuntimeWarning)
LOG = mklog('costs')

HZ = tuple(PREREG['primary_horizons'])
H_MAIN = 5
PHASES = ('TRAIN', 'VALID', 'OOS', 'LIVE-LIKE')
SEED = int(PREREG['seed'])
NSIM = int(PREREG['n_sim'])
NPERM = int(PREREG['n_perm'])
COSTS = tuple(PREREG['cost_bps'])
FOCUS = float(PREREG['cost_focus_bps'])
TAILS = tuple(PREREG['tail_pcts'])
LEAVE = float(PREREG['tail_leave_pct'])
ALPHA_P = float(PREREG['perm_alpha'])
MIN_XS = 30
LAG_NW = 19
MATCH_KEYS_MOM = ('mom20', 'mom60')
MATCH_KEYS_IND = ('mom20', 'total_mv', 'rv_20')
TOP_PCT = 0.10


# ══════════════════════════════════════════════════ 通用工具

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


def _p_two(t):
    return float(2.0 * norm.sf(abs(t))) if np.isfinite(t) else np.nan


def _daily_mean(v, dates):
    return pd.Series(v).groupby(dates).mean().values


def _z(df, cols):
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


# ══════════════════════════════════════════════════ §44 成本

def cost_rows(y, dates, tag, h):
    """事件层：对每个成本档把「往返成本」从每笔收益中直接扣除。"""
    y = np.asarray(y, dtype=np.float64)
    m = np.isfinite(y)
    y, dd = y[m], np.asarray(dates)[m]
    rows = []
    gross_mean = float(y.mean()) if len(y) else np.nan
    gross_dm = _daily_mean(y, dd)
    for c in COSTS:
        cc = 2.0 * float(c) / 1e4
        net = y - cc
        st = ic_stats(_daily_mean(net, dd), name='%s_h%d_net%d' % (tag, h, c))
        stg = ic_stats(gross_dm, name='%s_h%d_gross' % (tag, h))
        rows.append({'tag': tag, 'horizon': h, 'bps': float(c), 'cost_rt': cc,
                     'n': int(len(y)),
                     'gross_mean': gross_mean,
                     'gross_t_nw': stg['t_nw'],
                     'mean': float(net.mean()), 'median': float(np.median(net)),
                     'winrate': float((net > 0).mean()), 't_nw': st['t_nw'],
                     'p_two': _p_two(st['t_nw'])})
    return rows, gross_mean


def score_sel_cost(SCD, scol, h):
    """分数选择层：日内按分数取 top10% 等权，扣往返成本后重算。

    返回 (rows, per_trade_net0)。逐日序列未建模重叠持仓的资金占用，仅作量级参考。
    """
    ycol = 'exe_%d' % h
    d = SCD[['k', 'date', scol, ycol]].copy()
    f = np.isfinite(d[ycol].values) & np.isfinite(d[scol].values)
    d = d[f]
    r = d.groupby('k')[scol].rank(pct=True).values
    sel = r > 1.0 - TOP_PCT
    ds = d[sel]
    day_sel = ds.groupby('date')[ycol].mean()
    day_all = d.groupby('date')[ycol].mean()
    idx = day_sel.index.intersection(day_all.index)
    diff = (day_sel.loc[idx] - day_all.loc[idx]).values
    rows = []
    for c in COSTS:
        cc = 2.0 * float(c) / 1e4
        st = ic_stats(day_sel.values - cc, name='%s_h%d_sel%d' % (scol, h, c))
        sta = ic_stats(diff - cc, name='%s_h%d_alpha%d' % (scol, h, c))
        rows.append({'score': scol, 'horizon': h, 'bps': float(c), 'cost_rt': cc,
                     'n_trade': int(len(ds)), 'n_day': int(len(day_sel)),
                     'sel_mean_gross': float(ds[ycol].mean()),
                     'sel_mean_net': float(ds[ycol].mean() - cc),
                     'daily_t_nw': st['t_nw'],
                     'alpha_gross': float(np.nanmean(diff)),
                     'alpha_net': float(np.nanmean(diff) - cc),
                     'alpha_t_nw': sta['t_nw'], 'alpha_p_two': _p_two(sta['t_nw'])})
    return rows, ds[ycol].values


# ══════════════════════════════════════════════════ §46 尾部

def tail_rows(y, tag, h):
    y = np.asarray(y, dtype=np.float64)
    y = y[np.isfinite(y)]
    n = len(y)
    if n < 100:
        return [], {}
    ys = np.sort(y)[::-1]
    tot = float(ys.sum())
    pos = float(ys[ys > 0].sum())
    cs = np.cumsum(ys)
    half = int(np.searchsorted(cs, 0.5 * tot) + 1) if tot > 0 else -1
    desc = {'tag': tag, 'horizon': h, 'n': n,
            'gross_mean': float(ys.mean()), 'gross_median': float(np.median(ys)),
            'gross_sum': tot, 'pos_sum': pos,
            'n_for_half_sum': half,
            'sum_share_top1': float(ys[:max(1, int(np.ceil(0.01 * n)))].sum() / tot) if tot else np.nan,
            'sum_share_top5': float(ys[:max(1, int(np.ceil(0.05 * n)))].sum() / tot) if tot else np.nan,
            'sum_share_top10': float(ys[:max(1, int(np.ceil(0.10 * n)))].sum() / tot) if tot else np.nan,
            'pos_share_top5': float(ys[:max(1, int(np.ceil(0.05 * n)))].sum() / pos) if pos else np.nan}
    rows = []
    for p in TAILS:
        k = max(1, int(np.ceil(float(p) * n)))
        rest = ys[k:]
        rows.append({'tag': tag, 'horizon': h, 'tail_pct': float(p), 'n_removed': k,
                     'n_left': int(len(rest)),
                     'leave_mean': float(rest.mean()) if len(rest) else np.nan,
                     'leave_median': float(np.median(rest)) if len(rest) else np.nan,
                     'leave_winrate': float((rest > 0).mean()) if len(rest) else np.nan,
                     'removed_sum': float(ys[:k].sum()),
                     'removed_sum_share': float(ys[:k].sum() / tot) if tot else np.nan})
    return rows, desc


# ══════════════════════════════════════════════════ 全有效域 exe_h 池

def all_stocks_pool(h):
    cl = np.load(os.path.join(PAN, 'close.npy')).astype(np.float64)
    op = np.load(os.path.join(PAN, 'open.npy')).astype(np.float64)
    v = np.load(os.path.join(PAN, 'valid.npy')).astype(bool)
    NCODES, NCAL = cl.shape
    L = NCAL - h - 1
    num = np.full((NCODES, NCAL), np.nan, dtype=np.float64)
    ok = np.zeros((NCODES, NCAL), dtype=bool)
    if L > 0:
        num[:, :L] = cl[:, h:h + L] / op[:, 1:L + 1] - 1.0
        ok[:, :L] = v[:, :L] & v[:, 1:L + 1] & v[:, h:h + L]
    val = num[ok]
    return val[np.isfinite(val)]


# ══════════════════════════════════════════════════ 匹配反事实（与 P3 同实现）

def matched_counterfactual(E, TD, keys, ind_matched, h, tag):
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
    if ind_matched:
        bkey = pdate.astype(np.int64) * 100000 + pind.astype(np.int64)
        order = np.argsort(bkey, kind='stable')
        pkey_s = bkey[order]
        uk, ustart = np.unique(pkey_s, return_index=True)
        ucnt = np.diff(np.append(ustart, len(pkey_s)))
        kmap = {int(k): i for i, k in enumerate(uk)}
        ekeyb = edate.astype(np.int64) * 100000 + eind.astype(np.int64)
        e_of_bucket = {}
        for i in ev_idx[m_ev]:
            e_of_bucket.setdefault(int(ekeyb[i]), []).append(i)
        for b, eis in e_of_bucket.items():
            j = kmap.get(b)
            if j is None:
                continue
            sl = order[ustart[j]:ustart[j] + ucnt[j]]
            d2 = ((Zev[np.asarray(eis)][:, None, :] - pz[sl][None, :, :]) ** 2).sum(2)
            matched[np.asarray(eis)] = pex[sl][d2.argmin(1)]
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
            d2 = ((Zev[np.asarray(eis)][:, None, :] - pz[sl][None, :, :]) ** 2).sum(2)
            matched[np.asarray(eis)] = pex[sl][d2.argmin(1)]

    use = m_ev & np.isfinite(matched)
    diff = E[ycol].values[use] - matched[use]
    t = ic_stats(_daily_mean(diff, E['date'].values[use]))['t_nw']
    LOG('   [%s] n=%d  adj_alpha %+.4f  t %+.2f  (E1 %.4f - matched %.4f)'
        % (tag, int(use.sum()), float(diff.mean()), t,
           float(E[ycol].values[use].mean()), float(matched[use].mean())))
    return {'tag': tag, 'h': h, 'n': int(use.sum()),
            'e1_mean': float(E[ycol].values[use].mean()),
            'matched_mean': float(matched[use].mean()),
            'adj_alpha': float(diff.mean()), 't_nw': t}, matched[use]


# ══════════════════════════════════════════════════ N3 随机回撤（与 P3 §29 同估计量）

def random_pullback_null(E, TD, h, rng):
    ycol = 'exe_%d' % h
    NCAL = int(pload_meta()['NCAL'])
    ekey = E['code'].values.astype(np.int64) * NCAL + E['k'].values.astype(np.int64)
    tkey = TD['code'].values.astype(np.int64) * NCAL + TD['k'].values.astype(np.int64)
    keep = (~np.isin(tkey, ekey)) & np.isfinite(TD[ycol].values)
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
    e_legs = np.unique(E['code'].values.astype(np.int64) * BIG
                       + E['leg'].values.astype(np.int64))
    li = np.searchsorted(uk, e_legs)
    li = li[(li < len(uk)) & (uk[np.minimum(li, len(uk) - 1)] == e_legs)]
    if len(li) == 0:
        return None
    st, cn = ustart[li], ucnt[li]
    valid_pos = []
    for a, bct in zip(st, cn):
        idx = np.arange(a, a + bct)
        ok = k_s[idx]
        if ok.any():
            valid_pos.append(idx[ok])
    if not valid_pos:
        return None
    allpos = np.concatenate(valid_pos)
    allleg = np.concatenate([np.full(len(v), i, dtype=np.int64)
                             for i, v in enumerate(valid_pos)])
    nleg = len(valid_pos)
    o = np.argsort(allleg, kind='stable')
    allpos, allleg = allpos[o], allleg[o]
    lstart = np.searchsorted(allleg, np.arange(nleg), side='left')
    lcnt = np.searchsorted(allleg, np.arange(nleg), side='right') - lstart
    real = float(np.nanmean(E[ycol].values))
    null_means = np.empty(NSIM, dtype=np.float64)
    step = 100
    for s0 in range(0, NSIM, step):
        s1 = min(NSIM, s0 + step)
        u = rng.random((s1 - s0, nleg))
        pick = lstart[None, :] + (u * lcnt[None, :]).astype(np.int64)
        pick = np.minimum(pick, lstart[None, :] + lcnt[None, :] - 1)
        null_means[s0:s1] = np.nanmean(y_s[allpos[pick]], 1)
    return _null_rec('N3_RandomPullback', h, real, null_means, n=int(nleg), unit='leg')


def _null_rec(name, h, real, null_means, n, unit='event'):
    nm = np.asarray(null_means, dtype=np.float64)
    ge = int((nm >= real).sum())
    le = int((nm <= real).sum())
    p_hi = (1.0 + ge) / (1.0 + NSIM)
    p_lo = (1.0 + le) / (1.0 + NSIM)
    return {'null': name, 'h': h, 'n_unit': n, 'unit': unit,
            'real_mean': real, 'null_mean': float(nm.mean()),
            'null_sd': float(nm.std(ddof=1)),
            'null_p05': float(np.quantile(nm, 0.05)),
            'null_p95': float(np.quantile(nm, 0.95)),
            'p_real_ge_null': p_hi, 'p_real_le_null': p_lo,
            'p_two': float(min(1.0, 2.0 * min(p_hi, p_lo)))}


# ══════════════════════════════════════════════════ N2 随机日期

def random_date_null(E, TD, h, rng):
    ycol = 'exe_%d' % h
    cand = (TD['is_pb'].values == 0) & np.isfinite(TD[ycol].values)
    cc = TD['code'].values.astype(np.int64)
    yv = TD[ycol].values.astype(np.float64)
    o = np.argsort(cc, kind='stable')
    cc_s, y_s, ok_s = cc[o], yv[o], cand[o]
    uk, ustart = np.unique(cc_s, return_index=True)
    ucnt = np.diff(np.append(ustart, len(cc_s)))
    # 每个 code 内可用候选区间
    lstart = np.empty(len(uk), dtype=np.int64)
    lcnt = np.empty(len(uk), dtype=np.int64)
    pool_pos = []
    pool_of = []
    for j, (a, bct) in enumerate(zip(ustart, ucnt)):
        idx = np.arange(a, a + bct)
        idx = idx[ok_s[idx]]
        pool_pos.append(idx)
        pool_of.append(np.full(len(idx), j, dtype=np.int64))
    pp = np.concatenate(pool_pos)
    pf = np.concatenate(pool_of)
    o2 = np.argsort(pf, kind='stable')
    pp, pf = pp[o2], pf[o2]
    lstart = np.searchsorted(pf, np.arange(len(uk)), side='left')
    lcnt = np.searchsorted(pf, np.arange(len(uk)), side='right') - lstart
    cmap = {int(c): j for j, c in enumerate(uk)}
    ec = E['code'].values.astype(np.int64)
    ej = np.array([cmap.get(int(c), -1) for c in ec])
    good = (ej >= 0) & (lcnt[np.maximum(ej, 0)] > 0) & np.isfinite(E[ycol].values)
    if good.sum() < 50:
        return None
    gj = ej[good]
    real = float(E[ycol].values[good].mean())
    ng = len(gj)
    means = np.empty(NSIM, dtype=np.float64)
    step = 100
    ls, lc = lstart[gj], lcnt[gj]
    for s0 in range(0, NSIM, step):
        s1 = min(NSIM, s0 + step)
        u = rng.random((s1 - s0, ng))
        pick = ls[None, :] + (u * lc[None, :]).astype(np.int64)
        pick = np.minimum(pick, ls[None, :] + lc[None, :] - 1)
        means[s0:s1] = y_s[pp[pick]].mean(1)
    return _null_rec('N2_RandomDate', h, real, means, n=ng, unit='event')


def bootstrap_null(name, real, pool_vals, n, rng):
    pool_vals = np.asarray(pool_vals, dtype=np.float64)
    pool_vals = pool_vals[np.isfinite(pool_vals)]
    if len(pool_vals) < 30:
        return None
    means = np.empty(NSIM, dtype=np.float64)
    step = 100
    for s0 in range(0, NSIM, step):
        s1 = min(NSIM, s0 + step)
        idx = rng.integers(0, len(pool_vals), size=(s1 - s0, n))
        means[s0:s1] = pool_vals[idx].mean(1)
    return _null_rec(name, H_MAIN, real, means, n=n, unit='event')


def random_stock_null(h, n_event, rng):
    pool = all_stocks_pool(h)
    real = np.nan
    means = np.empty(NSIM, dtype=np.float64)
    step = 100
    for s0 in range(0, NSIM, step):
        s1 = min(NSIM, s0 + step)
        idx = rng.integers(0, len(pool), size=(s1 - s0, n_event))
        means[s0:s1] = pool[idx].mean(1)
    return pool, means


# ══════════════════════════════════════════════════ §49 置换引擎

def perm_engine(RS_c, RT_c, Yc, kk, nperm, rng, min_xs=MIN_XS):
    """日内置换分数（等价于置换回撤质量分数的日内归属）。

    RS_c/RT_c/Yc 为「按日连续排列」的紧凑序列（已剔除当日样本外个股）。
    置换不改变当日分数秩的均值与离差 → 分母 Sxx/Syy 不变，只需重算分子。
    """
    u, starts, counts = np.unique(kk, return_index=True, return_counts=True)
    nd = len(starts)
    day = np.repeat(np.arange(nd), counts)
    n = counts.astype(np.float64)
    rsc = RS_c - np.repeat(np.add.reduceat(RS_c, starts) / n, counts)
    rtc = RT_c - np.repeat(np.add.reduceat(RT_c, starts) / n, counts)
    Sxx = np.add.reduceat(rsc * rsc, starts)
    Syy = np.add.reduceat(rtc * rtc, starts)
    ok = (counts >= min_xs) & (Sxx > 0) & (Syy > 0)
    Pc = Yc.astype(np.float64)
    P = np.add.reduceat(Pc, starts)
    N = counts - P
    okA = (counts >= min_xs) & (P > 0) & (N > 0)
    denA = np.where(okA, P * N, np.nan)
    offA = P * (P + 1) / 2.0

    def _ic_from(nump):
        v = np.full(nd, np.nan)
        v[ok] = nump[ok] / np.sqrt(Sxx[ok] * Syy[ok])
        return v

    def _auc_from(RYp):
        v = np.full(nd, np.nan)
        v[okA] = (RYp[okA] - offA[okA]) / denA[okA]
        return v

    ic_obs = _ic_from(np.add.reduceat(rsc * rtc, starts))
    auc_obs = _auc_from(np.add.reduceat(RS_c * Pc, starts))
    ic_n = np.full(nperm, np.nan)
    auc_n = np.full(nperm, np.nan)
    for i in range(nperm):
        order = np.lexsort((rng.random(len(RS_c)), day))
        RSp = RS_c[order]
        ic_n[i] = np.nanmean(_ic_from(np.add.reduceat(RSp * rtc, starts)))
        auc_n[i] = np.nanmean(_auc_from(np.add.reduceat(RSp * Pc, starts)))
    return ic_obs, auc_obs, ic_n, auc_n


# ══════════════════════════════════════════════════ 主流程

def main():
    t0 = time.time()
    LOG.sep('=')
    LOG('H-TREND-PULLBACK-01  P5 成本 / 尾部 / Null / 置换 / 多重检验  %s'
        % time.strftime('%Y-%m-%d %H:%M:%S'))
    LOG('成本口径：往返 = 单边 bp × 2（open[k+1] 买 / close[k+H] 卖，各一次）；'
        'focus = %dbps' % int(FOCUS))
    LOG('随机化：seed=%d  n_sim=%d  n_perm=%d  perm_alpha=%.3f  tail_leave=%.2f'
        % (SEED, NSIM, NPERM, ALPHA_P, LEAVE))
    LOG('声明：本脚本只覆盖 §44 / §46 / §48 / §49 / §50；G 编号权威定义以用户提交规格 §51/§52 为准。')

    M = pload_meta()
    NCODES, NCAL = int(M['NCODES']), int(M['NCAL'])
    day = pload_day()
    TD = pd.read_parquet(os.path.join(DATA, 'hp_trenddays.parquet'))
    EV = pd.read_parquet(os.path.join(DATA, 'hp_events.parquet'))
    E2 = pd.read_parquet(os.path.join(DATA, 'hp_events_e2.parquet'))
    SC = pd.read_parquet(os.path.join(DATA, 'hp_model_score.parquet'))
    LOG('   trenddays %d / E1 %d / E2 %d / score %d'
        % (len(TD), len(EV), len(E2), len(SC)))

    scols = ['s_m1', 's_m2']
    SCD = SC.merge(TD[['code', 'k', 'date', 'year', 'ind_l1']
                      + ['exe_%d' % h for h in HZ]],
                   on=['code', 'k'], how='left')
    if len(SCD) != len(SC):
        LOG('   [WARN] score 与 trenddays 合并不完全匹配（%d vs %d），按可得行继续'
            % (len(SCD), len(SC)))
    SCD = SCD.sort_values('k', kind='stable').reset_index(drop=True)
    LOG('   SAMPLE（分数可得）%d 行，覆盖 %d 个交易日'
        % (len(SCD), SCD['k'].nunique()))

    rng = np.random.default_rng(SEED)
    out_cost_ev, out_cost_sc, out_tail, out_tail_desc = [], [], [], []
    out_null, out_gates = [], []

    # ══════════════════════════════════════════ §44 成本敏感性
    LOG.sep()
    LOG('§44 成本敏感性（G14：focus = %dbps 后仍须具经济意义）' % int(FOCUS))
    for nm, E in (('E1', EV), ('E2', E2)):
        for h in HZ:
            rows, gm = cost_rows(E['exe_%d' % h].values, E['date'].values, nm, h)
            out_cost_ev.extend(rows)
            if h == H_MAIN:
                be = gm * 1e4 / 2.0
                LOG('   %s h=%d  n=%d  gross %+.4f  ->  盈亏平衡成本 %.1fbps（往返）'
                    % (nm, h, rows[0]['n'], gm, be))
                for r in rows:
                    LOG('     %4dbps  net %+.4f  median %+.4f  win %.3f  t %+.2f'
                        % (r['bps'], r['mean'], r['median'], r['winrate'], r['t_nw']))
    LOG('   分数选择层（SAMPLE 内逐日 top10% 等权；未建模重叠持仓资金占用，仅量级参考）')
    for scol in scols:
        for h in HZ:
            rows, ysel = score_sel_cost(SCD, scol, h)
            out_cost_sc.extend(rows)
            if h == H_MAIN:
                for r in rows:
                    LOG('     %s h=%d %4dbps  sel_net %+.4f  超额(净) %+.4f  t %+.2f'
                        % (scol, h, r['bps'], r['sel_mean_net'], r['alpha_net'],
                           r['alpha_t_nw']))

    # ══════════════════════════════════════════ §46 尾部依赖
    LOG.sep()
    LOG('§46 尾部依赖与右尾贡献（G13：Leave-Top%.0f%% 后 alpha 不得转负）' % (LEAVE * 100))
    tail_objs = [('E1', EV['exe_%d' % H_MAIN].values),
                 ('E2', E2['exe_%d' % H_MAIN].values)]
    for scol in scols:
        d = SCD[['k', scol, 'exe_%d' % H_MAIN]].copy()
        d = d[np.isfinite(d[scol].values) & np.isfinite(d['exe_%d' % H_MAIN].values)]
        rr = d.groupby('k')[scol].rank(pct=True).values
        tail_objs.append(('%s_TOP10' % scol, d.loc[rr > 1.0 - TOP_PCT, 'exe_%d' % H_MAIN].values))
    for tag, y in tail_objs:
        rows, desc = tail_rows(y, tag, H_MAIN)
        out_tail.extend(rows)
        if desc:
            out_tail_desc.append(desc)
        if rows:
            LOG('   %-10s n=%6d  gross %+.4f  median %+.4f  |  '
                '右尾贡献 top1%% %.2f / top5%% %.2f / top10%% %.2f（占总额）  半数总额需 %d 笔'
                % (tag, desc['n'], desc['gross_mean'], desc['gross_median'],
                   desc['sum_share_top1'], desc['sum_share_top5'],
                   desc['sum_share_top10'], desc['n_for_half_sum']))
            for r in rows:
                LOG('     leave-top%-4.0f%%  n_left=%6d  mean %+.4f  median %+.4f  win %.3f'
                    % (r['tail_pct'] * 100, r['n_left'], r['leave_mean'],
                       r['leave_median'], r['leave_winrate']))

    # ══════════════════════════════════════════ §48 Null 模型
    LOG.sep()
    LOG('§48 Null 模型 N1 - N6（n_sim=%d，seed=%d；p 单侧/双侧全报）' % (NSIM, SEED))
    real_e1 = float(np.nanmean(EV['exe_%d' % H_MAIN].values))
    LOG('   真实 E1 参照：h=%d mean %+.4f  n=%d'
        % (H_MAIN, real_e1, int(np.isfinite(EV['exe_%d' % H_MAIN].values).sum())))

    # N1 全有效域随机 stock-day
    pool, means = random_stock_null(H_MAIN, int(np.isfinite(
        EV['exe_%d' % H_MAIN].values).sum()), rng)
    LOG('   N1_RandomStock  池=%d 全有效域均值 %+.4f  Null mean %+.4f (sd %.4f)'
        % (len(pool), float(pool.mean()), float(means.mean()), float(means.std(ddof=1))))
    out_null.append(_null_rec('N1_RandomStock', H_MAIN, real_e1, means,
                              n=int(np.isfinite(EV['exe_%d' % H_MAIN].values).sum()),
                              unit='stock-day'))
    for r in out_null:
        LOG('      经验 p：真实>=Null %.4f ；双侧 %.4f' % (r['p_real_ge_null'], r['p_two']))

    # N2 随机日期
    r2 = random_date_null(EV, TD, H_MAIN, rng)
    if r2:
        out_null.append(r2)
        LOG('   N2_RandomDate  可用事件=%d  Null mean %+.4f (sd %.4f)  p(真实>=Null) %.4f'
            % (r2['n_unit'], r2['null_mean'], r2['null_sd'], r2['p_real_ge_null']))

    # N3 随机回撤（= P3 §29 同一估计量）
    r3 = random_pullback_null(EV, TD, H_MAIN, rng)
    if r3:
        out_null.append(r3)
        LOG('   N3_RandomPullback  leg=%d  Null mean %+.4f (sd %.4f)  p(真实>=Null) %.4f'
            % (r3['n_unit'], r3['null_mean'], r3['null_sd'], r3['p_real_ge_null']))

    # N4 / N5 匹配反事实自助
    for tag, keys, ind_m in (('N4_MomentumMatched', list(MATCH_KEYS_MOM), False),
                             ('N5_IndustryMomentumMatched', list(MATCH_KEYS_IND), True)):
        rec, matched = matched_counterfactual(EV, TD, keys, ind_m, H_MAIN, tag)
        r = bootstrap_null(tag, rec['e1_mean'], matched, rec['n'], rng)
        if r:
            r['matched_mean'] = rec['matched_mean']
            r['adj_alpha'] = rec['adj_alpha']
            out_null.append(r)
            LOG('   %-26s matched_mean %+.4f  adj_alpha %+.4f  p(真实>=Null) %.4f'
                % (tag, rec['matched_mean'], rec['adj_alpha'], r['p_real_ge_null']))

    # ══════════════════════════════════════════ §49 置换检验（含 N6）
    LOG.sep()
    LOG('§49 置换检验：分数 s_m1 日内随机置换（n_perm=%d）；N6 为同一估计量，共用本次模拟'
        % NPERM)
    kk = SCD['k'].values.astype(np.int64)
    RS_all, RT_all, Y_all = {}, {}, {}
    for scol in scols:
        P = np.full((NCAL, NCODES), np.nan, dtype=np.float32)
        P[kk, SCD['code'].values.astype(np.int64)] = SCD[scol].values.astype(np.float32)
        T = np.full((NCAL, NCODES), np.nan, dtype=np.float32)
        T[kk, SCD['code'].values.astype(np.int64)] = SCD['exe_%d' % H_MAIN].values.astype(np.float32)
        V = np.isfinite(P) & np.isfinite(T)
        RSp = _row_ranks(np.where(V, P, np.nan))
        RTp = _row_ranks(np.where(V, T, np.nan))
        thr = np.nanquantile(np.where(V, T, np.nan), 1.0 - TOP_PCT, axis=1)
        ci = SCD['code'].values.astype(np.int64)
        RS_all[scol] = RSp[kk, ci]
        RT_all[scol] = RTp[kk, ci]
        Y_all[scol] = np.where(V, T, np.nan)[kk, ci] >= thr[kk]
        LOG('   [%s] 观测（日等权）IC %+.4f  AUC %.4f  （应与 P4 TRAIN_FIT 一致）'
            % (scol, float(np.nanmean(_row_ic(RS_all[scol], RT_all[scol], kk))),
               float(np.nanmean(_row_auc(RS_all[scol], Y_all[scol], kk)))))

    perm_res, perm_ic_obs, perm_auc_obs = {}, {}, {}
    for scol in scols:
        ic_obs, auc_obs, ic_n, auc_n = perm_engine(
            RS_all[scol], RT_all[scol], Y_all[scol], kk, NPERM, rng)
        o_ic = float(np.nanmean(ic_obs))
        o_auc = float(np.nanmean(auc_obs))
        perm_ic_obs[scol], perm_auc_obs[scol] = o_ic, o_auc
        p_hi = (1.0 + int((ic_n >= o_ic).sum())) / (1.0 + NPERM)
        p_lo = (1.0 + int((ic_n <= o_ic).sum())) / (1.0 + NPERM)
        pa_hi = (1.0 + int((auc_n >= o_auc).sum())) / (1.0 + NPERM)
        perm_res[scol] = {'score': scol, 'stat': 'IC', 'obs': o_ic,
                          'null_mean': float(np.nanmean(ic_n)),
                          'null_sd': float(np.nanstd(ic_n, ddof=1)),
                          'null_p05': float(np.nanquantile(ic_n, 0.05)),
                          'null_p95': float(np.nanquantile(ic_n, 0.95)),
                          'p_hi': p_hi, 'p_lo': p_lo,
                          'p_two': float(min(1.0, 2 * min(p_hi, p_lo))), 'n_perm': NPERM}
        perm_res[scol + '_auc'] = {'score': scol, 'stat': 'AUC_top10', 'obs': o_auc,
                                   'null_mean': float(np.nanmean(auc_n)),
                                   'null_sd': float(np.nanstd(auc_n, ddof=1)),
                                   'null_p05': float(np.nanquantile(auc_n, 0.05)),
                                   'null_p95': float(np.nanquantile(auc_n, 0.95)),
                                   'p_hi': pa_hi, 'p_lo': np.nan,
                                   'p_two': float(min(1.0, 2 * pa_hi)), 'n_perm': NPERM}
        r = perm_res[scol]
        LOG('   [%s] IC  观测 %+.4f  Null %+.4f (sd %.4f)  单侧 p %.4f  双侧 %.4f'
            % (scol, r['obs'], r['null_mean'], r['null_sd'], r['p_hi'], r['p_two']))
        ra = perm_res[scol + '_auc']
        LOG('   [%s] AUC 观测 %.4f  Null %.4f (sd %.4f)  单侧 p %.4f'
            % (scol, ra['obs'], ra['null_mean'], ra['null_sd'], ra['p_hi']))

    # N6 = 主置换检验（同一估计量），仅登记一次模拟结果
    r6 = dict(perm_res['s_m1'])
    r6['null'] = 'N6_ShuffledPullbackQuality'
    r6['unit'] = 'score-day'
    r6['n_unit'] = len(SCD)
    r6['real_mean'] = perm_ic_obs['s_m1']
    r6['null_mean_dist'] = r6['null_mean']
    r6['p_real_ge_null'] = r6['p_hi']
    r6['p_real_le_null'] = r6['p_lo']
    out_null.append({k: r6[k] for k in
                     ('null', 'h', 'n_unit', 'unit', 'real_mean', 'null_mean',
                      'null_sd', 'null_p05', 'null_p95',
                      'p_real_ge_null', 'p_real_le_null', 'p_two') if k in r6}
                    | {'h': H_MAIN})
    LOG('   N6_ShuffledPullbackQuality：观测 IC %+.4f  Null %+.4f  p %.4f（与上行同一次模拟）'
        % (perm_ic_obs['s_m1'], r6['null_mean'], r6['p_hi']))

    # ══════════════════════════════════════════ 量化分档（§50 quantile 家族）
    LOG.sep()
    LOG('§50 quantile 家族：日内十分位首尾档价差（模型分数 × 视界）')
    rows_q = []
    for scol in scols:
        for h in HZ:
            ycol = 'exe_%d' % h
            d = SCD[['k', 'date', 'year', scol, ycol]].copy()
            d = d[np.isfinite(d[scol].values) & np.isfinite(d[ycol].values)]
            rr = d.groupby('k')[scol].rank(pct=True).values
            hi = d.loc[rr > 0.9].groupby('date')[ycol].mean()
            lo = d.loc[rr <= 0.1].groupby('date')[ycol].mean()
            idx = hi.index.intersection(lo.index)
            sp = (hi.loc[idx] - lo.loc[idx]).values
            st = ic_stats(sp, name='%s_h%d_qspread' % (scol, h))
            rows_q.append({'score': scol, 'horizon': h, 'n_day': st['n'],
                           'q_spread': st['mean'], 't_nw': st['t_nw'],
                           'p_two': _p_two(st['t_nw'])})
            LOG('     %s h=%-2d  日=%4d  spread %+.4f  t %+.2f'
                % (scol, h, st['n'], st['mean'], st['t_nw']))

    # ══════════════════════════════════════════ §50 多重检验
    LOG.sep()
    LOG('§50 多重检验（BH-FDR，alpha=%.2f；families = %s）'
        % (PREREG['fdr_alpha'], PREREG['fdr_families']))
    fam = []
    p_ev = os.path.join(OUTD, 'hp_single_events.csv')
    if os.path.exists(p_ev):
        d = pd.read_csv(p_ev)
        d = d[(d['phase'] == 'ALL') & np.isfinite(d['t_nw'])]
        for _, r in d.iterrows():
            fam.append({'family': 'horizon',
                        'test': '%s_h%d_%s' % (r['anchor'], r['horizon'], r['phase']),
                        'stat': r['mean'], 't_now': r['t_nw'], 'p_raw': _p_two(r['t_nw'])})
    p_ic = os.path.join(OUTD, 'hp_single_ic.csv')
    if os.path.exists(p_ic):
        d = pd.read_csv(p_ic)
        d = d[d['horizon'] == H_MAIN]
        for _, r in d.iterrows():
            for pn in PHASES:
                t = r.get('ic_t_%s' % pn, np.nan)
                if np.isfinite(t):
                    fam.append({'family': 'feature',
                                'test': '%s_%s_h%d' % (r['feature'], pn, H_MAIN),
                                'stat': r.get('ic_%s' % pn, np.nan),
                                't_now': t, 'p_raw': _p_two(t)})
    p_me = os.path.join(OUTD, 'hp_model_eval.csv')
    if os.path.exists(p_me):
        d = pd.read_csv(p_me)
        d = d[(d['phase'] == 'ALL') & d['tag'].isin(['TRAIN_FIT', 'OOS_FIT'])
              & np.isfinite(d['ic_t'])]
        for _, r in d.iterrows():
            fam.append({'family': 'model',
                        'test': '%s_%s_h%d' % (r['model'], r['tag'], r['horizon']),
                        'stat': r['ic'], 't_now': r['ic_t'], 'p_raw': _p_two(r['ic_t'])})
    for fn, lab, key in (('hp_model_wf.csv', 'wf', 'ic_t'),
                         ('hp_model_year.csv', 'year', 'ic_t'),
                         ('hp_model_regime.csv', 'regime', 'ic_t')):
        fp = os.path.join(OUTD, fn)
        if not os.path.exists(fp):
            continue
        d = pd.read_csv(fp)
        for _, r in d.iterrows():
            t = r.get(key, np.nan)
            if not np.isfinite(t):
                continue
            nm = r.get('test_year', r.get('year', r.get('regime', '?')))
            fam.append({'family': 'window', 'test': '%s_%s' % (lab, nm),
                        'stat': r.get('ic', np.nan), 't_now': t, 'p_raw': _p_two(t)})
    for r in rows_q:
        fam.append({'family': 'quantile',
                    'test': '%s_h%d' % (r['score'], r['horizon']),
                    'stat': r['q_spread'], 't_now': r['t_nw'], 'p_two': None,
                    'p_raw': r['p_two']})
    FDR = pd.DataFrame(fam)
    if len(FDR):
        FDR['q_bh'], FDR['reject'] = np.nan, False
        for f_, sub in FDR.groupby('family'):
            rej, q = bh_fdr(sub['p_raw'].values, alpha=PREREG['fdr_alpha'])
            FDR.loc[sub.index, 'q_bh'] = q
            FDR.loc[sub.index, 'reject'] = rej
        rej_all, q_all = bh_fdr(FDR['p_raw'].values, alpha=PREREG['fdr_alpha'])
        FDR['q_bh_pooled'] = q_all
        FDR['reject_pooled'] = rej_all
        for f_ in PREREG['fdr_families']:
            sub = FDR[FDR['family'] == f_]
            if len(sub):
                LOG('   %-9s 检验 %3d 条  原始 p<%.2f 的 %3d 条  BH 通过 %3d 条'
                    % (f_, len(sub), PREREG['fdr_alpha'],
                       int((sub['p_raw'] < PREREG['fdr_alpha']).sum()),
                       int(sub['reject'].sum())))
            else:
                LOG('   %-9s 无可用检验（如实标注，不静默）' % f_)
        LOG('   合并（pooled）%d 条检验，BH 通过 %d 条'
            % (len(FDR), int(FDR['reject_pooled'].sum())))

    # ══════════════════════════════════════════ Gate 判定（§44 G14 / §46 G13 / §49 G15）
    LOG.sep()
    LOG('Gate 判定（P5 覆盖 G13 / G14 / G15；阈值取 PREREG 冻结值，不得事后调整）')
    e1_cost = [r for r in out_cost_ev if r['tag'] == 'E1' and r['horizon'] == H_MAIN]
    e1_focus = [r for r in e1_cost if r['bps'] == FOCUS][0]
    e1_base = [r for r in e1_cost if r['bps'] == 0][0]
    tl = [r for r in out_tail if r['tag'] == 'E1' and r['horizon'] == H_MAIN]
    tl_leave = [r for r in tl if abs(r['tail_pct'] - LEAVE) < 1e-9][0]
    perm_m1 = perm_res['s_m1']

    g14 = 'PASS' if (e1_focus['mean'] > 0 and PREREG['cost_require_positive']) else 'FAIL'
    g13 = 'PASS' if tl_leave['leave_mean'] > 0 else 'FAIL'
    g15 = 'PASS' if perm_m1['p_hi'] < ALPHA_P else 'FAIL'
    LOG('   G14 成本（E1 h=%d @%dbps）        : net %+.4f（gross %+.4f）  -> %s'
        % (H_MAIN, int(FOCUS), e1_focus['mean'], e1_base['gross_mean'], g14))
    LOG('   G13 尾部（E1 h=%d Leave-Top%.0f%%）: mean %+.4f  -> %s'
        % (H_MAIN, LEAVE * 100, tl_leave['leave_mean'], g13))
    LOG('   G15 置换（s_m1 IC 单侧 p）        : p %.4f（阈值 <%.2f）  -> %s'
        % (perm_m1['p_hi'], ALPHA_P, g15))
    LOG('   附：E2 @%dbps net %+.4f ；s_m1 选择层 @%dbps 净超额 %+.4f'
        % (int(FOCUS),
           [r for r in out_cost_ev if r['tag'] == 'E2' and r['horizon'] == H_MAIN
            and r['bps'] == FOCUS][0]['mean'], int(FOCUS),
           [r for r in out_cost_sc if r['score'] == 's_m1' and r['horizon'] == H_MAIN
            and r['bps'] == FOCUS][0]['alpha_net']))
    out_gates.append({'gate': 'G14', 'layer': 'P5/§44', 'lethal': 1,
                      'criterion': 'E1 h=%d 净收益 @%dbps > 0' % (H_MAIN, int(FOCUS)),
                      'value': e1_focus['mean'], 'verdict': g14})
    out_gates.append({'gate': 'G13', 'layer': 'P5/§46', 'lethal': 0,
                      'criterion': 'E1 h=%d Leave-Top%.0f%% 均值 > 0' % (H_MAIN, LEAVE * 100),
                      'value': tl_leave['leave_mean'], 'verdict': g13})
    out_gates.append({'gate': 'G15', 'layer': 'P5/§49', 'lethal': 0,
                      'criterion': 's_m1 IC 置换单侧 p < %.2f' % ALPHA_P,
                      'value': perm_m1['p_hi'], 'verdict': g15})
    LOG('   致命 Gate 属本层者为 G14；G14=%s' % g14)
    LOG('   TRADING_AUTHORIZATION = NO')

    # ══════════════════════════════════════════ 落盘
    pd.DataFrame(out_cost_ev).to_csv(os.path.join(OUTD, 'hp_costs_event.csv'), index=False)
    pd.DataFrame(out_cost_sc).to_csv(os.path.join(OUTD, 'hp_costs_score.csv'), index=False)
    pd.DataFrame(out_tail).to_csv(os.path.join(OUTD, 'hp_costs_tail.csv'), index=False)
    pd.DataFrame(out_tail_desc).to_csv(os.path.join(OUTD, 'hp_costs_tail_desc.csv'), index=False)
    pd.DataFrame(out_null).to_csv(os.path.join(OUTD, 'hp_costs_null.csv'), index=False)
    pd.DataFrame(list(perm_res.values())).to_csv(os.path.join(OUTD, 'hp_costs_perm.csv'),
                                                 index=False)
    pd.DataFrame(rows_q).to_csv(os.path.join(OUTD, 'hp_costs_quantile.csv'), index=False)
    FDR.to_csv(os.path.join(OUTD, 'hp_costs_fdr.csv'), index=False)
    pd.DataFrame(out_gates).to_csv(os.path.join(OUTD, 'hp_costs_gates.csv'), index=False)
    LOG.sep()
    LOG('落盘: out/hp_costs_event.csv / _score.csv / _tail.csv / _tail_desc.csv / _null.csv /')
    LOG('      _perm.csv / _quantile.csv / _fdr.csv / _gates.csv')
    LOG('P5 完成 %.0fs' % (time.time() - t0))
    LOG.sep('=')
    print('DONE')
    return 0


def _row_ic(RS_c, RT_c, kk, min_xs=MIN_XS):
    """紧凑序列上的逐日 IC（仅用于日志对照）"""
    u, starts, counts = np.unique(kk, return_index=True, return_counts=True)
    n = counts.astype(np.float64)
    rsc = RS_c - np.repeat(np.add.reduceat(RS_c, starts) / n, counts)
    rtc = RT_c - np.repeat(np.add.reduceat(RT_c, starts) / n, counts)
    Sxx = np.add.reduceat(rsc * rsc, starts)
    Syy = np.add.reduceat(rtc * rtc, starts)
    num = np.add.reduceat(rsc * rtc, starts)
    out = np.full(len(starts), np.nan)
    ok = (counts >= min_xs) & (Sxx > 0) & (Syy > 0)
    out[ok] = num[ok] / np.sqrt(Sxx[ok] * Syy[ok])
    return out


def _row_auc(RS_c, Yc, kk, min_xs=MIN_XS):
    """紧凑序列上的逐日 top-decile AUC（仅用于日志对照）"""
    u, starts, counts = np.unique(kk, return_index=True, return_counts=True)
    P = np.add.reduceat(Yc.astype(np.float64), starts)
    N = counts - P
    RY = np.add.reduceat(RS_c * Yc, starts)
    out = np.full(len(starts), np.nan)
    ok = (counts >= min_xs) & (P > 0) & (N > 0)
    out[ok] = (RY[ok] - P[ok] * (P[ok] + 1) / 2.0) / (P[ok] * N[ok])
    return out


if __name__ == '__main__':
    sys.exit(main())
