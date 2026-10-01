# -*- coding: utf-8 -*-
"""HVE-Research V1 公共库

口径约定
--------
* 事件日 t = HVE 发生日（收盘后可知）
* 基准买入价 = 事件日收盘价 close[t]（另提供次日开盘价 open[t+1] 变体）
* T+k 收益 = close[t+k] / close[t] - 1（k 为该股自身交易日）
* MFE/MAE 取 t+1 .. t+k 区间的最高价/最低价相对 close[t]
* 超额收益 = 个券收益 - 同期全市场等权收益（同一日历窗口）
"""
import os
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

DATA = r'D:\mystock\hve_research\data'
HORIZONS = [1, 3, 5, 10, 20, 40, 60]
SEED = 20260930

# ---------------- 载入 ----------------
def load_panel():
    p = pd.read_parquet(os.path.join(DATA, 'panel.parquet'))
    m = pd.read_parquet(os.path.join(DATA, 'market.parquet'))
    cal = pd.read_parquet(os.path.join(DATA, 'calendar.parquet'))
    return p, m, cal


def add_mkt_fwd(market: pd.DataFrame, horizons=HORIZONS) -> pd.DataFrame:
    """按日历日的市场等权前瞻收益 mkt_fwd_k[td_idx]"""
    m = market.sort_values('td_idx').reset_index(drop=True)
    r = m['ew_ret'].fillna(0.0).values
    n = len(r)
    lr = np.log1p(r)
    cs = np.concatenate([[0.0], np.cumsum(lr)])
    # mkt_fwd_h[i] = 第 i+1 .. i+h 个交易日的市场等权累计收益
    for h in horizons:
        out = np.full(n, np.nan)
        if n > h:
            out[:n - h] = np.exp(cs[h + 1:n + 1] - cs[1:n - h + 1]) - 1.0
        m[f'mkt_fwd_{h}'] = out
    # 中位数口径（A股右尾极厚，中位数基准远比均值基准严格）
    r2 = m['med_ret'].fillna(0.0).values
    lr2 = np.log1p(r2)
    cs2 = np.concatenate([[0.0], np.cumsum(lr2)])
    for h in horizons:
        out = np.full(n, np.nan)
        if n > h:
            out[:n - h] = np.exp(cs2[h + 1:n + 1] - cs2[1:n - h + 1]) - 1.0
        m[f'mktmed_fwd_{h}'] = out
    return m


# ---------------- 前瞻结果 ----------------
def compute_outcomes(panel: pd.DataFrame, horizons=HORIZONS, save=True):
    """为每一行计算前瞻收益/MFE/MAE（按个股自身交易日序）"""
    p = panel
    codes, _ = pd.factorize(p['ts_code'], sort=False)
    n = len(p)
    close = p['close'].values.astype(np.float64)
    high = p['high'].values.astype(np.float64)
    low = p['low'].values.astype(np.float64)
    opn = p['open'].values.astype(np.float64)
    vol = p['vol'].values.astype(np.float64)

    res = {}
    for h in horizons:
        res[f'ret_{h}'] = np.full(n, np.nan)
        res[f'mfe_{h}'] = np.full(n, np.nan)
        res[f'mae_{h}'] = np.full(n, np.nan)
        res[f'maxret_{h}'] = np.full(n, np.nan)
        res[f'minret_{h}'] = np.full(n, np.nan)
        res[f'open_ret_{h}'] = np.full(n, np.nan)
        res[f'volr_{h}'] = np.full(n, np.nan)   # T+1..T+h 均量 / 事件日量

    # 个股切片边界
    chg = np.flatnonzero(np.diff(codes)) + 1
    starts = np.concatenate([[0], chg])
    ends = np.concatenate([chg, [n]])

    for s, e in zip(starts, ends):
        L = e - s
        if L < 2:
            continue
        c = close[s:e]; hi = high[s:e]; lo = low[s:e]; op = opn[s:e]; vv = vol[s:e]
        for h in horizons:
            if L > h:
                res[f'ret_{h}'][s:e - h] = c[h:] / c[:L - h] - 1.0
                res[f'open_ret_{h}'][s:e - h] = c[h:] / op[1:L - h + 1] - 1.0
            # 窗口 [t+1, t+h]
            Hpad = np.concatenate([hi, np.full(h, np.nan)])
            Lpad = np.concatenate([lo, np.full(h, np.nan)])
            Cpad = np.concatenate([c, np.full(h, np.nan)])
            Vpad = np.concatenate([vv, np.full(h, np.nan)])
            wH = sliding_window_view(Hpad, h)[1:L + 1]
            wL = sliding_window_view(Lpad, h)[1:L + 1]
            wC = sliding_window_view(Cpad, h)[1:L + 1]
            wV = sliding_window_view(Vpad, h)[1:L + 1]
            res[f'mfe_{h}'][s:e] = np.nanmax(wH, axis=1) / c - 1.0
            res[f'mae_{h}'][s:e] = np.nanmin(wL, axis=1) / c - 1.0
            res[f'maxret_{h}'][s:e] = np.nanmax(wC, axis=1) / c - 1.0
            res[f'minret_{h}'][s:e] = np.nanmin(wC, axis=1) / c - 1.0
            res[f'volr_{h}'][s:e] = np.nanmean(wV, axis=1) / np.where(vv > 0, vv, np.nan)

    out = pd.DataFrame(res)
    out.insert(0, 'row', np.arange(n, dtype=np.int64))
    for c in out.columns:
        if c != 'row':
            out[c] = out[c].astype(np.float32)
    if save:
        out.to_parquet(os.path.join(DATA, 'outcomes.parquet'), index=False)
    return out


# ---------------- 统计 ----------------
def stat(r, costs_bp=0.0, excess=None, label=''):
    """一组收益的完整统计"""
    r = np.asarray(r, dtype=np.float64)
    keep = ~np.isnan(r)
    r = r[keep]
    if excess is not None:
        excess = np.asarray(excess, dtype=np.float64)[keep]
    r = r - costs_bp / 1e4
    n = len(r)
    if n == 0:
        return dict(label=label, n=0)
    w = r[r > 0]; l = r[r <= 0]
    pf = (w.sum() / abs(l.sum())) if (len(l) > 0 and l.sum() != 0) else np.nan
    sd = r.std(ddof=1) if n > 1 else np.nan
    t = (r.mean() / (sd / np.sqrt(n))) if (n > 1 and sd > 0) else np.nan
    srt = np.sort(r)[::-1]
    k5 = max(int(np.ceil(n * 0.05)), 1)
    k10 = max(int(np.ceil(n * 0.10)), 1)
    d = dict(
        label=label, n=n,
        win=float((r > 0).mean()),
        mean=float(r.mean()), median=float(np.median(r)),
        mean_win=float(w.mean()) if len(w) else np.nan,
        mean_loss=float(l.mean()) if len(l) else np.nan,
        pf=float(pf) if pf == pf else np.nan,
        sd=float(sd) if sd == sd else np.nan,
        t=float(t) if t == t else np.nan,
        p05=float(np.percentile(r, 5)), p95=float(np.percentile(r, 95)),
        mfe=float(np.nan), mae=float(np.nan),
        top5_share=float(srt[:k5].sum() / r.sum()) if r.sum() != 0 else np.nan,
        top10_share=float(srt[:k10].sum() / r.sum()) if r.sum() != 0 else np.nan,
    )
    if excess is not None:
        d['exc_mean'] = float(excess.mean())
        d['exc_win'] = float((excess > 0).mean())
        sd2 = excess.std(ddof=1) if n > 1 else np.nan
        d['exc_t'] = float(excess.mean() / (sd2 / np.sqrt(n))) if (n > 1 and sd2 > 0) else np.nan
    return d


def stat_full(df, ret_col, mfe_col=None, mae_col=None, costs_bp=0.0,
              exc_col=None, label=''):
    r = df[ret_col].values.astype(np.float64) - costs_bp / 1e4
    m = ~np.isnan(r)
    r = r[m]
    d = stat(r, 0.0, None, label)
    if mfe_col is not None and mfe_col in df.columns:
        d['mfe'] = float(np.nanmean(df[mfe_col].values[m]))
    if mae_col is not None and mae_col in df.columns:
        d['mae'] = float(np.nanmean(df[mae_col].values[m]))
    if exc_col is not None and exc_col in df.columns:
        e = df[exc_col].values.astype(np.float64)[m]
        e = e[~np.isnan(e)]
        if len(e):
            d['exc_mean'] = float(e.mean()); d['exc_win'] = float((e > 0).mean())
            sd = e.std(ddof=1) if len(e) > 1 else np.nan
            d['exc_t'] = float(e.mean() / (sd / np.sqrt(len(e)))) if (len(e) > 1 and sd > 0) else np.nan
    return d


def welch_t(a, b):
    """Welch t 检验（a vs b）"""
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
    # 正态近似 p 值
    from scipy import stats
    p = 2 * (1 - stats.t.cdf(abs(t), df))
    return float(t), float(p)


# ---------------- 尾部集中度 ----------------
def concentration(r, label=''):
    r = np.asarray(r, dtype=np.float64); r = r[~np.isnan(r)]
    if len(r) < 20:
        return dict(label=label, n=len(r))
    srt = np.sort(r)[::-1]
    out = dict(label=label, n=len(r), mean_all=float(r.mean()),
               median_all=float(np.median(r)), pf_all=float(
                   r[r > 0].sum() / abs(r[r <= 0].sum()) if (r <= 0).any() and r[r <= 0].sum() != 0 else np.nan))
    for q in (0.05, 0.10):
        k = int(np.floor(len(r) * q))
        rr = srt[k:]
        out[f'mean_ex_top{int(q*100)}'] = float(rr.mean())
        out[f'median_ex_top{int(q*100)}'] = float(np.median(rr))
        out[f'pf_ex_top{int(q*100)}'] = float(
            rr[rr > 0].sum() / abs(rr[rr <= 0].sum()) if (rr <= 0).any() and rr[rr <= 0].sum() != 0 else np.nan)
        out[f'top{int(q*100)}_contrib'] = float(srt[:k].sum() / r.sum()) if r.sum() != 0 else np.nan
    return out


def rows_to_records(rows):
    df = pd.DataFrame(rows)
    return df
