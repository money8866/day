# -*- coding: utf-8 -*-
"""E09-V02 公共库

口径（预注册，冻结于 e09_v02_definitions.json）
----------------------------------------------
* 事件日 S = 启动日（收盘后可知）
* 整理窗 L = 2 / 3 / 4（三个版本分别统计，主定义 L=3）
* 缩量     = Mean(Vol[S+1..S+L]) / Vol[S] <= 0.70（主）；0.60 / 0.80 为预注册扰动
* 结构保持 = Min(Low[S+1..S+L]) >= Close[S] * 0.95
* 确认日 B = [S+L+1, S+10] 内首个满足条件的交易日
            （主口径：缩量窗口在入场时已完整观察到，无未来函数）
            （另存 spec 字面口径 [S+2, S+10] 作为对照变体）
* B1 价格突破 / B2 放量突破(量比>=1.2) / B3 强放量突破(量比>=1.5)
* 收益 = 后复权收盘价之比；MFE/MAE 取区间最高/最低价相对入场价
* 超额 = 个券收益 - 同期全市场等权收益（同一日历窗口）
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, r'D:\mystock\hve_research')
import hve_lib as L  # noqa: E402

ROOT = r'D:\mystock\e09_v02'
DATA = os.path.join(ROOT, 'data')
OUT = os.path.join(ROOT, 'out')
PANEL = r'D:\mystock\hve_research\data\panel.parquet'
MKTPATH = r'D:\mystock\hve_research\data\market.parquet'

SEED = 20261002
RNG = np.random.default_rng(SEED)

# ---- 预注册主参数 ----
P_RET = 3.0          # 启动日涨幅门槛(%)
P_VR = 1.5           # 启动日 Volume / MA20_Volume（含当日）
P_L = 3              # 整理长度主定义
L_GRID = [2, 3, 4]   # 预注册整理长度
VRB_GRID = [1.3, 1.5, 1.8, 2.0]     # 启动量比扰动
RET_GRID = [2.5, 3.0, 3.5, 4.0]     # 启动涨幅扰动
CONTR_GRID = [0.60, 0.70, 0.80]     # 缩量扰动
DD_GRID = [0.03, 0.05, 0.07]        # 最大回撤扰动（结构保持阈值 = 1 - dd）
BRK_VOL = {'A': 0.0, 'B': 1.2, 'C': 1.5}   # E09-A/B/C 突破量比门槛
W = 22               # 偏移矩阵宽度（需覆盖 B<=S+10 且 B+10<=S+20）
HOR_S = [1, 3, 5, 10, 20]
HOR_B = [1, 3, 5, 10]
COSTS = [0, 15, 30, 50]
COST_MAIN = 30
NB = 5000            # bootstrap 次数
IS_END = 20231231    # IS = 起始 ~ 2023-12-31；OOS = 2024-01-01 ~
MIN_OBS = 60         # 最小历史窗口（交易日）


# ---------------- 滚动（严格按个股切块）----------------
def roll_stat(vals, n, stat='mean', shift=1, minp=None):
    from numpy.lib.stride_tricks import sliding_window_view
    m = len(vals)
    minp = n if minp is None else minp
    out = np.full(m, np.nan)
    if m <= n:
        return out
    xp = np.concatenate([np.full(n, np.nan), vals])
    win = sliding_window_view(xp, n)
    w = win[:m] if shift else win[1:m + 1]
    cnt = np.sum(~np.isnan(w), axis=1)
    ok = cnt >= minp
    if stat == 'mean':
        out[ok] = np.nansum(w, axis=1)[ok] / cnt[ok]
    elif stat == 'max':
        out[ok] = np.nanmax(w, axis=1)[ok]
    elif stat == 'min':
        out[ok] = np.nanmin(w, axis=1)[ok]
    return out


def add_roll(p, arr, n, stat, shift, minp, newname):
    """arr 可为列名(str)或数组"""
    v = p[arr].values.astype(np.float64) if isinstance(arr, str) else np.asarray(arr, np.float64)
    out = np.full(len(v), np.nan)
    codes = p['_c'].values
    brk = np.flatnonzero(np.diff(codes)) + 1
    starts = np.concatenate([[0], brk])
    ends = np.concatenate([brk, [len(v)]])
    for s, e in zip(starts, ends):
        out[s:e] = roll_stat(v[s:e], n, stat, shift, minp)
    p[newname] = out
    return out


# ---------------- 统计 ----------------
def stat(r, costs_bp=0.0, excess=None, label=''):
    r = np.asarray(r, dtype=np.float64)
    keep = ~np.isnan(r)
    r = r[keep]
    if excess is not None:
        excess = np.asarray(excess, dtype=np.float64)[keep]
    r = r - costs_bp / 1e4
    n = len(r)
    if n == 0:
        return dict(label=label, n=0)
    w = r[r > 0]
    l = r[r <= 0]
    pf = (w.sum() / abs(l.sum())) if (len(l) > 0 and l.sum() != 0) else np.nan
    sd = r.std(ddof=1) if n > 1 else np.nan
    t = (r.mean() / (sd / np.sqrt(n))) if (n > 1 and sd > 0) else np.nan
    srt = np.sort(r)[::-1]
    k5 = max(int(np.ceil(n * 0.05)), 1)
    d = dict(label=label, n=n, win=float((r > 0).mean()), mean=float(r.mean()),
             median=float(np.median(r)), pf=float(pf) if pf == pf else np.nan,
             sd=float(sd) if sd == sd else np.nan, t=float(t) if t == t else np.nan,
             p05=float(np.percentile(r, 5)), p95=float(np.percentile(r, 95)),
             top5_share=float(srt[:k5].sum() / r.sum()) if r.sum() != 0 else np.nan)
    if excess is not None:
        e = excess[~np.isnan(excess)]
        if len(e):
            d['exc'] = float(e.mean())
            d['exc_win'] = float((e > 0).mean())
            sd2 = e.std(ddof=1) if len(e) > 1 else np.nan
            d['exc_t'] = float(e.mean() / (sd2 / np.sqrt(len(e)))) if (len(e) > 1 and sd2 > 0) else np.nan
    return d


def _cluster_bs_mean(r, cl, nboot, seed):
    """向量化 cluster bootstrap：按簇聚合 sum/count 后整簇重抽"""
    u, inv = np.unique(cl, return_inverse=True)
    G = len(u)
    if G < 5:
        return None
    S = np.bincount(inv, weights=r, minlength=G)
    N = np.bincount(inv, minlength=G).astype(np.float64)
    rg = np.random.default_rng(seed)
    pick = rg.integers(0, G, size=(nboot, G))
    ss = S[pick].sum(axis=1)
    nn = N[pick].sum(axis=1)
    return ss / np.where(nn > 0, nn, np.nan)


def boot_ci(r, nboot=NB, cluster=None, seed=SEED):
    """bootstrap 95% CI；cluster 给定时按簇抽样（cluster bootstrap）"""
    r = np.asarray(r, dtype=np.float64)
    cl_all = np.asarray(cluster) if cluster is not None else None
    m = ~np.isnan(r)
    r = r[m]
    if cl_all is not None:
        cl_all = cl_all[m]
    n = len(r)
    if n < 10:
        return np.nan, np.nan, np.nan
    rg = np.random.default_rng(seed)
    if cl_all is None:
        idx = rg.integers(0, n, size=(nboot, n))
        bs = np.nanmean(r[idx], axis=1)
    else:
        bs = _cluster_bs_mean(r, cl_all, nboot, seed)
        if bs is None:
            idx = rg.integers(0, n, size=(nboot, n))
            bs = np.nanmean(r[idx], axis=1)
    lo, hi = np.nanpercentile(bs, [2.5, 97.5])
    return float(np.nanmean(bs)), float(lo), float(hi)


def cluster_boot_pvalue(r, nboot=NB, cluster=None, seed=SEED, h0=0.0):
    """双侧 bootstrap p 值：检验 mean(r) 是否显著异于 h0"""
    r = np.asarray(r, dtype=np.float64)
    cl_all = np.asarray(cluster) if cluster is not None else None
    if cl_all is not None:
        m = ~np.isnan(r)
        r = r[m]
        cl_all = cl_all[m]
    else:
        r = r[~np.isnan(r)]
    n = len(r)
    if n < 10:
        return np.nan
    rg = np.random.default_rng(seed)
    if cl_all is None:
        idx = rg.integers(0, n, size=(nboot, n))
        bs = np.nanmean(r[idx], axis=1)
    else:
        bs = _cluster_bs_mean(r, cl_all, nboot, seed)
        if bs is None:
            idx = rg.integers(0, n, size=(nboot, n))
            bs = np.nanmean(r[idx], axis=1)
    obs = float(np.nanmean(r)) - h0
    c = bs - np.nanmean(bs)
    return float(2.0 * min((c >= abs(obs)).mean(), (c <= -abs(obs)).mean()))


def welch_t(a, b):
    from scipy import stats
    a = np.asarray(a, dtype=np.float64); a = a[~np.isnan(a)]
    b = np.asarray(b, dtype=np.float64); b = b[~np.isnan(b)]
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return np.nan, np.nan
    va, vb = a.var(ddof=1), b.var(ddof=1)
    se = np.sqrt(va / na + vb / nb)
    if se == 0:
        return np.nan, np.nan
    t = (a.mean() - b.mean()) / se
    df = (va / na + vb / nb) ** 2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    return float(t), float(2 * (1 - stats.t.cdf(abs(t), df)))


def bh_fdr(pvals):
    """Benjamini-Hochberg 校正，返回 adjusted p"""
    p = np.asarray(pvals, dtype=np.float64)
    ok = ~np.isnan(p)
    out = np.full(len(p), np.nan)
    pv = p[ok]
    m = len(pv)
    if m == 0:
        return out
    order = np.argsort(pv)
    ranked = pv[order]
    adj = ranked * m / (np.arange(1, m + 1))
    adj = np.minimum.accumulate(adj[::-1])[::-1]
    adj = np.clip(adj, 0, 1)
    res = np.empty(m)
    res[order] = adj
    out[ok] = res
    return out


# ---------------- OLS（含按个股聚类稳健标准误）----------------
def ols_cluster(y, X, groups, add_const=True):
    """返回 dict: beta, se_cluster, t, r2, n"""
    y = np.asarray(y, dtype=np.float64)
    X = np.asarray(X, dtype=np.float64)
    m = np.isfinite(y) & np.isfinite(X).all(axis=1)
    y, X, g = y[m], X[m], np.asarray(groups)[m]
    if add_const:
        X = np.column_stack([np.ones(len(X)), X])
    n, k = X.shape
    if n < k + 5:
        return None
    XtX_inv = np.linalg.pinv(X.T @ X)
    beta = XtX_inv @ (X.T @ y)
    resid = y - X @ beta
    # 聚类稳健：先按簇排序，再用切片（避免在整列上反复布尔索引）
    order = np.argsort(g, kind='stable')
    Xs, rs, gs = np.ascontiguousarray(X[order]), resid[order], g[order]
    _, starts = np.unique(gs, return_index=True)
    ends = np.append(starts[1:], len(gs))
    meat = np.zeros((k, k))
    for a, b in zip(starts, ends):
        Xu = Xs[a:b]
        ru = rs[a:b]
        s = Xu.T @ ru
        meat += np.outer(s, s)
    G = len(starts)
    fac = (G / (G - 1)) * ((n - 1) / (n - k)) if G > 1 and n > k else 1.0
    V = XtX_inv @ meat @ XtX_inv * fac
    se = np.sqrt(np.clip(np.diag(V), 0, None))
    tstat = np.where(se > 0, beta / se, np.nan)
    ss_tot = ((y - y.mean()) ** 2).sum()
    r2 = 1.0 - (resid ** 2).sum() / ss_tot if ss_tot > 0 else np.nan
    adj_r2 = 1.0 - (1 - r2) * (n - 1) / (n - k) if n > k else np.nan
    return dict(beta=beta, se=se, t=tstat, r2=float(r2), adj_r2=float(adj_r2),
                n=n, k=k, n_groups=G)


def concentration(r):
    r = np.asarray(r, dtype=np.float64)
    r = r[~np.isnan(r)]
    if len(r) < 20:
        return {}
    srt = np.sort(r)[::-1]
    o = dict(n=len(r), mean=float(r.mean()), median=float(np.median(r)))
    for q in (0.05, 0.10):
        k = int(np.floor(len(r) * q))
        rr = srt[k:]
        o[f'mean_ex_top{int(q*100)}'] = float(rr.mean())
        o[f'top{int(q*100)}_contrib'] = float(srt[:k].sum() / r.sum()) if r.sum() != 0 else np.nan
    return o


def save(df, name):
    path = os.path.join(OUT, name)
    df.to_csv(path, index=False, encoding='utf-8-sig')
    print(f'  -> {name}  ({len(df)} rows)', flush=True)
