#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Technical Indicators
====================
Self-contained, vectorized numpy/pandas indicators. No external TA library
dependency, so the system runs anywhere lightgbm + numpy are installed.

All functions accept numpy arrays (or pandas Series). NaN-safe where it
matters for rolling windows.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

EPS = 1e-9


# ============================================================
# Moving averages
# ============================================================
def sma(x, period: int) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    if period <= 1 or len(x) < period:
        return np.full_like(x, np.nan)
    return pd.Series(x).rolling(period, min_periods=period).mean().to_numpy()


def ema(x, period: int) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    if period <= 1 or len(x) == 0:
        return x.copy()
    return pd.Series(x).ewm(span=period, adjust=False, min_periods=period).mean().to_numpy()


def rma(x, period: int) -> np.ndarray:
    """Running moving average (Wilder's)."""
    x = np.asarray(x, dtype=float)
    if period <= 1 or len(x) == 0:
        return x.copy()
    alpha = 1.0 / period
    return pd.Series(x).ewm(alpha=alpha, adjust=False, min_periods=period).mean().to_numpy()


# ============================================================
# Returns / momentum
# ============================================================
def roc(x, period: int) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    out = np.full_like(x, np.nan)
    if period <= 0 or len(x) <= period:
        return out
    out[period:] = (x[period:] / x[:-period] - 1.0) * 100.0
    return out


def returns(x, period: int = 1) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    out = np.full_like(x, np.nan)
    if period <= 0 or len(x) <= period:
        return out
    out[period:] = x[period:] / x[:-period] - 1.0
    return out


def slope(x, period: int) -> float:
    """Linear regression slope of the last ``period`` points, normalized."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < period or period < 2:
        return 0.0
    seg = x[-period:]
    if np.any(~np.isfinite(seg)):
        seg = seg[np.isfinite(seg)]
    if len(seg) < 2:
        return 0.0
    y = np.arange(len(seg), dtype=float)
    denom = (len(seg) * np.sum(y * y) - np.sum(y) ** 2)
    if abs(denom) < EPS:
        return 0.0
    b = (len(seg) * np.sum(y * seg) - np.sum(y) * np.sum(seg)) / denom
    base = float(np.nanmean(np.abs(seg))) + EPS
    return float(b / base)


# ============================================================
# Volatility & risk
# ============================================================
def volatility(x, period: int = 20, annualize: bool = False) -> float:
    """Std of daily returns over the last ``period`` bars."""
    x = np.asarray(x, dtype=float)
    if len(x) < period + 1 or period < 2:
        return 0.0
    r = np.diff(x[-period - 1:]) / x[-period - 1:-1]
    v = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
    return v * (np.sqrt(252) if annualize else 1.0)


def atr(high, low, close, period: int = 14) -> np.ndarray:
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    n = len(close)
    if n < period + 1:
        return np.full(n, np.nan)
    prev_close = np.concatenate([[close[0]], close[:-1]])
    tr = np.maximum.reduce([
        high - low,
        np.abs(high - prev_close),
        np.abs(low - prev_close),
    ])
    return rma(tr, period)


def natr(high, low, close, period: int = 14) -> np.ndarray:
    a = atr(high, low, close, period)
    close = np.asarray(close, dtype=float)
    return a / (close + EPS) * 100.0


def max_drawdown(x) -> float:
    x = np.asarray(x, dtype=float)
    if len(x) < 2:
        return 0.0
    idx = np.isfinite(x)
    x = x[idx]
    if len(x) < 2:
        return 0.0
    running_max = np.maximum.accumulate(x)
    dd = (x - running_max) / (running_max + EPS)
    return float(np.min(dd))


def ulcer_index(x, period: int = 60) -> float:
    x = np.asarray(x, dtype=float)
    if len(x) < period:
        period = len(x)
    if period < 2:
        return 0.0
    seg = x[-period:]
    running_max = np.maximum.accumulate(seg)
    dd_pct = (seg - running_max) / (running_max + EPS) * 100.0
    return float(np.sqrt(np.mean(dd_pct ** 2)))


def sharpe_ratio(x, period: int = 60, rf: float = 0.03) -> float:
    x = np.asarray(x, dtype=float)
    if len(x) < period + 1 or period < 2:
        return 0.0
    r = np.diff(x[-period - 1:]) / x[-period - 1:-1]
    if len(r) < 2:
        return 0.0
    mu = float(np.mean(r))
    sd = float(np.std(r, ddof=1))
    if sd < EPS:
        return 0.0
    daily_rf = rf / 252.0
    return (mu - daily_rf) / sd * np.sqrt(252)


def sortino_ratio(x, period: int = 60, rf: float = 0.03) -> float:
    x = np.asarray(x, dtype=float)
    if len(x) < period + 1 or period < 2:
        return 0.0
    r = np.diff(x[-period - 1:]) / x[-period - 1:-1]
    if len(r) < 2:
        return 0.0
    mu = float(np.mean(r))
    daily_rf = rf / 252.0
    downside = r[r < daily_rf] - daily_rf
    if len(downside) < 1:
        return 0.0
    dd_std = float(np.sqrt(np.mean(downside ** 2)))
    if dd_std < EPS:
        return 0.0
    return (mu - daily_rf) / dd_std * np.sqrt(252)


def calmar_ratio(x, period: int = 252) -> float:
    x = np.asarray(x, dtype=float)
    if len(x) < 2:
        return 0.0
    seg = x[-period:] if len(x) >= period else x
    ret = float(seg[-1] / seg[0] - 1.0)
    mdd = abs(max_drawdown(seg))
    if mdd < EPS:
        return 0.0
    return ret / mdd


# ============================================================
# Trend oscillators
# ============================================================
def rsi(x, period: int = 14) -> float:
    x = np.asarray(x, dtype=float)
    if len(x) < period + 1:
        return 50.0
    delta = np.diff(x[-period - 1:])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = float(np.mean(gain))
    avg_loss = float(np.mean(loss))
    if avg_loss < EPS:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def macd(x, fast: int = 12, slow: int = 26, signal: int = 9):
    x = np.asarray(x, dtype=float)
    ema_fast = ema(x, fast)
    ema_slow = ema(x, slow)
    macd_line = ema_fast - ema_slow
    signal_line = pd.Series(macd_line).ewm(span=signal, adjust=False).mean().to_numpy()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def adx(high, low, close, period: int = 14) -> float:
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    n = len(close)
    if n < period * 2:
        return 25.0
    a = atr(high, low, close, period)
    if np.any(a <= 0):
        return 25.0
    up_move = np.diff(high, prepend=high[0])
    down_move = -np.diff(low, prepend=low[0])
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_di = rma(plus_dm, period) / (a + EPS) * 100.0
    minus_di = rma(minus_dm, period) / (a + EPS) * 100.0
    dx = np.abs(plus_di - minus_di) / (plus_di + minus_di + EPS) * 100.0
    adx_arr = rma(dx, period)
    val = adx_arr[-1]
    return float(np.clip(val, 0, 100)) if np.isfinite(val) else 25.0


def kdj(high, low, close, period: int = 9):
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    n = len(close)
    if n < period:
        return 50.0, 50.0, 50.0
    hh = pd.Series(high).rolling(period, min_periods=1).max().to_numpy()
    ll = pd.Series(low).rolling(period, min_periods=1).min().to_numpy()
    rsv = (close - ll) / (hh - ll + EPS) * 100.0
    k = pd.Series(rsv).ewm(alpha=1.0 / 3.0, adjust=False).mean().to_numpy()
    d = pd.Series(k).ewm(alpha=1.0 / 3.0, adjust=False).mean().to_numpy()
    j = 3.0 * k - 2.0 * d
    return float(k[-1]), float(d[-1]), float(j[-1])


# ============================================================
# Breakout / strength
# ============================================================
def breakout_pct(close, high, period: int = 60) -> float:
    """How far the last close sits above the prior ``period`` high (%, can be <0)."""
    close = np.asarray(close, dtype=float)
    high = np.asarray(high, dtype=float)
    if len(close) < period + 1:
        return 0.0
    prior_high = float(np.max(high[-period - 1:-1]))
    return (close[-1] - prior_high) / (prior_high + EPS) * 100.0


def new_high_count(high, period: int = 60) -> int:
    high = np.asarray(high, dtype=float)
    if len(high) < period:
        return 0
    seg = high[-period:]
    cur = seg[-1]
    return int(np.sum(seg <= cur + EPS))


def price_position(close, high, low, period: int = 60) -> float:
    close = np.asarray(close, dtype=float)
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    if len(close) < period:
        period = len(close)
    if period < 2:
        return 0.5
    h = float(np.max(high[-period:]))
    l = float(np.min(low[-period:]))
    if h - l < EPS:
        return 0.5
    return float((close[-1] - l) / (h - l))


# ============================================================
# Volume / breadth helpers
# ============================================================
def volume_ratio(vol, period: int = 20) -> float:
    vol = np.asarray(vol, dtype=float)
    if len(vol) < period + 1 or period < 1:
        return 1.0
    avg = float(np.mean(vol[-period - 1:-1]))
    if avg < EPS:
        return 1.0
    return float(vol[-1] / avg)


def consecutive_up_days(pct_chg) -> int:
    pct_chg = np.asarray(pct_chg, dtype=float)
    if len(pct_chg) == 0:
        return 0
    count = 0
    for v in pct_chg[::-1]:
        if v > 0:
            count += 1
        else:
            break
    return count


def above_ema_days(close, period: int = 20) -> int:
    close = np.asarray(close, dtype=float)
    if len(close) < period:
        return 0
    e = ema(close, period)
    count = 0
    for i in range(len(close) - 1, -1, -1):
        if np.isfinite(e[i]) and close[i] > e[i]:
            count += 1
        else:
            break
    return count


# ============================================================
# Cross-sectional helpers
# ============================================================
def percentile_rank(values) -> np.ndarray:
    """Cross-sectional percentile rank (0-100) within the given array."""
    arr = np.asarray(values, dtype=float)
    if len(arr) == 0:
        return arr
    finite = np.isfinite(arr)
    out = np.full(len(arr), np.nan)
    if finite.sum() < 2:
        return out
    sub = arr[finite]
    order = sub.argsort()
    ranks = np.empty(len(sub), dtype=float)
    ranks[order] = np.arange(1, len(sub) + 1)
    out[finite] = (ranks - 1) / (len(sub) - 1) * 100.0
    return out


def zscore(values) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    out = np.full_like(arr, np.nan)
    finite = np.isfinite(arr)
    if finite.sum() < 2:
        return out
    sub = arr[finite]
    mu, sd = float(np.mean(sub)), float(np.std(sub, ddof=1))
    if sd < EPS:
        return out
    out[finite] = (sub - mu) / sd
    return out


def winsorize(values, lower: float = 0.01, upper: float = 0.99) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    finite = np.isfinite(arr)
    if finite.sum() < 2:
        return arr
    lo = float(np.quantile(arr[finite], lower))
    hi = float(np.quantile(arr[finite], upper))
    return np.clip(arr, lo, hi)


# ============================================================
# Relative strength & beta
# ============================================================
def relative_strength(etf_close, bench_close) -> float:
    """RS ratio = etf_close / bench_close (last value)."""
    etf_close = np.asarray(etf_close, dtype=float)
    bench_close = np.asarray(bench_close, dtype=float)
    if len(etf_close) == 0 or len(bench_close) == 0:
        return 1.0
    b = bench_close[-1]
    if abs(b) < EPS:
        return 1.0
    return float(etf_close[-1] / b)


def beta(etf_close, bench_close, period: int = 60) -> float:
    etf_close = np.asarray(etf_close, dtype=float)
    bench_close = np.asarray(bench_close, dtype=float)
    n = min(len(etf_close), len(bench_close))
    if n < period + 1 or period < 2:
        return 1.0
    e = etf_close[-period - 1:]
    b = bench_close[-period - 1:]
    re = np.diff(e) / e[:-1]
    rb = np.diff(b) / b[:-1]
    mask = np.isfinite(re) & np.isfinite(rb)
    if mask.sum() < 5:
        return 1.0
    re, rb = re[mask], rb[mask]
    var_b = float(np.var(rb, ddof=1))
    if var_b < EPS:
        return 0.0
    cov = float(np.cov(re, rb, ddof=1)[0, 1])
    return cov / var_b


def rolling_corr(a, b, period: int = 20) -> float:
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    n = min(len(a), len(b))
    if n < period + 1:
        return 0.0
    a, b = a[-period - 1:], b[-period - 1:]
    mask = np.isfinite(a) & np.isfinite(b)
    if mask.sum() < 5:
        return 0.0
    sa, sb = a[mask], b[mask]
    if np.std(sa) < EPS or np.std(sb) < EPS:
        return 0.0
    return float(np.corrcoef(sa, sb)[0, 1])


def hurst_exponent(x) -> float:
    """R/S Hurst exponent over the given series (0.5 = random walk)."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 32:
        return 0.5
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 32:
        return 0.5
    lags = range(2, min(20, n // 2))
    tau = []
    rs_list = []
    for lag in lags:
        diffs = x[lag:] - x[:-lag]
        if len(diffs) < 2:
            continue
        m = np.mean(diffs)
        dev = diffs - m
        cumdev = np.cumsum(dev)
        r = float(np.max(cumdev) - np.min(cumdev))
        s = float(np.std(diffs, ddof=1))
        if s < EPS:
            continue
        tau.append(lag)
        rs_list.append(r / s)
    if len(tau) < 3:
        return 0.5
    coeffs = np.polyfit(np.log(tau), np.log(rs_list), 1)
    h = float(coeffs[0])
    return float(np.clip(h, 0.0, 1.0))
