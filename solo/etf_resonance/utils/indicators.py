"""All TA indicators self-implemented without TA-Lib.

Every function is fully vectorized using numpy/pandas rolling/ewm.
Supports both Series and 2D numpy array inputs for batch processing.
"""

import numpy as np
import pandas as pd
from typing import Union, Optional, Tuple
from scipy import stats

NumericArray = Union[np.ndarray, pd.Series]


# ──────────────────────────────────────────────
# Moving Averages
# ──────────────────────────────────────────────

def ema(close: NumericArray, period: int = 20) -> np.ndarray:
    """Exponential Moving Average using pandas ewm (fully vectorized)."""
    s = pd.Series(np.asarray(close))
    return s.ewm(span=period, adjust=False).mean().values


def sma(close: NumericArray, period: int = 20) -> np.ndarray:
    """Simple Moving Average."""
    s = pd.Series(np.asarray(close))
    return s.rolling(period, min_periods=1).mean().values


def rma(close: NumericArray, period: int = 14) -> np.ndarray:
    """Wilder's Smoothed Moving Average (RMA)."""
    alpha = 1.0 / period
    arr = np.asarray(close, dtype=np.float64)
    out = np.full_like(arr, np.nan)
    out[:period] = np.mean(arr[:period])
    for i in range(period, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out


# ──────────────────────────────────────────────
# True Range & ATR
# ──────────────────────────────────────────────

def true_range(high: NumericArray, low: NumericArray, close: NumericArray,
               prev_close: Optional[NumericArray] = None) -> np.ndarray:
    """True Range: max(high-low, |high-prev_close|, |low-prev_close|)."""
    h, l, c = np.asarray(high), np.asarray(low), np.asarray(close)
    if prev_close is not None:
        pc = np.asarray(prev_close)
    else:
        pc = np.roll(c, 1)
        pc[0] = c[0]
    tr = np.maximum(h - l, np.abs(h - pc))
    tr = np.maximum(tr, np.abs(l - pc))
    return tr


def atr(high: NumericArray, low: NumericArray, close: NumericArray,
        period: int = 14) -> np.ndarray:
    """Average True Range using RMA."""
    tr = true_range(high, low, close)
    return rma(tr, period)


# ──────────────────────────────────────────────
# ADX (Average Directional Index)
# ──────────────────────────────────────────────

def adx(high: NumericArray, low: NumericArray, close: NumericArray,
        period: int = 14) -> np.ndarray:
    """ADX - Average Directional Index (fully vectorized)."""
    h, l, c = np.asarray(high), np.asarray(low), np.asarray(close)
    length = len(c)

    up_move = np.diff(h, prepend=h[0])
    down_move = np.diff(l, prepend=l[0])

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = true_range(h, l, c)
    atr_val = rma(tr, period)

    plus_di = 100 * rma(plus_dm, period) / np.maximum(atr_val, 1e-10)
    minus_di = 100 * rma(minus_dm, period) / np.maximum(atr_val, 1e-10)

    dx = 100 * np.abs(plus_di - minus_di) / np.maximum(plus_di + minus_di, 1e-10)
    return rma(dx, period)


# ──────────────────────────────────────────────
# Slope / Linear Regression
# ──────────────────────────────────────────────

def slope(close: NumericArray, period: int = 14) -> np.ndarray:
    """Linear regression slope over rolling window (percent per day)."""
    arr = np.asarray(close, dtype=np.float64)
    out = np.full_like(arr, np.nan)
    x = np.arange(period)
    for i in range(period - 1, len(arr)):
        y = arr[i - period + 1: i + 1]
        if np.any(np.isnan(y)):
            continue
        slope_val, _ = np.polyfit(x, y, 1)
        out[i] = slope_val / np.maximum(np.mean(y), 1e-10) * 100
    return out


# ──────────────────────────────────────────────
# Hurst Exponent (trend strength indicator)
# ──────────────────────────────────────────────

def hurst_exponent(close: NumericArray, max_lag: int = 20) -> float:
    """Hurst Exponent: >0.5 trending, <0.5 mean-reverting."""
    arr = np.asarray(close, dtype=np.float64)
    arr = arr[~np.isnan(arr)]
    if len(arr) < max_lag * 2:
        return 0.5
    lags = range(2, max_lag)
    tau = [np.std(arr[lag:] - arr[:-lag]) for lag in lags]
    if any(t == 0 for t in tau):
        return 0.5
    poly = np.polyfit(np.log(lags), np.log(tau), 1)
    return float(poly[0]) / 2.0


# ──────────────────────────────────────────────
# Rolling Window & Statistics
# ──────────────────────────────────────────────

def rolling_window(arr: np.ndarray, window: int) -> np.ndarray:
    """Efficient rolling window view using numpy strides."""
    shape = (max(0, arr.shape[0] - window + 1), window)
    strides = (arr.strides[0], arr.strides[0])
    return np.lib.stride_tricks.as_strided(arr, shape=shape, strides=strides)


def rank_score(arr: NumericArray, period: int = 60) -> np.ndarray:
    """Percentile rank over rolling window (0-100)."""
    s = pd.Series(np.asarray(arr))
    rank = s.rolling(period, min_periods=1).apply(
        lambda x: stats.percentileofscore(x, x[-1]) if len(x) > 0 else 50.0, raw=True
    )
    return rank.values


def normalize(arr: NumericArray) -> np.ndarray:
    """Min-max normalize to [0, 1]."""
    a = np.asarray(arr, dtype=np.float64)
    mn, mx = np.nanmin(a), np.nanmax(a)
    if mx == mn:
        return np.full_like(a, 0.5)
    return (a - mn) / (mx - mn)


def zscore(arr: NumericArray) -> np.ndarray:
    """Z-score normalization."""
    a = np.asarray(arr, dtype=np.float64)
    mu, sigma = np.nanmean(a), np.nanstd(a)
    if sigma == 0:
        return np.zeros_like(a)
    return (a - mu) / sigma


def winsorize(arr: NumericArray, limits: float = 0.01) -> np.ndarray:
    """Winsorize extreme values."""
    a = np.asarray(arr, dtype=np.float64)
    lower, upper = np.percentile(a[~np.isnan(a)], [limits * 100, (1 - limits) * 100])
    return np.clip(a, lower, upper)


def max_drawdown(close: NumericArray) -> float:
    """Maximum drawdown as a positive percentage."""
    arr = np.asarray(close, dtype=np.float64)
    peak = np.maximum.accumulate(arr)
    dd = (arr - peak) / np.maximum(peak, 1e-10)
    return float(np.abs(np.nanmin(dd)) * 100)


def sharpe_ratio(returns: NumericArray, periods_per_year: int = 252) -> float:
    """Annualized Sharpe Ratio."""
    r = np.asarray(returns, dtype=np.float64)
    r = r[~np.isnan(r)]
    if len(r) < 2:
        return 0.0
    return float(np.mean(r) / np.maximum(np.std(r, ddof=1), 1e-10) * np.sqrt(periods_per_year))


def calmar_ratio(close: NumericArray, periods_per_year: int = 252) -> float:
    """Calmar Ratio: annualized return / max drawdown."""
    arr = np.asarray(close, dtype=np.float64)
    total_return = (arr[-1] / np.maximum(arr[0], 1e-10)) - 1
    ann_return = (1 + total_return) ** (periods_per_year / max(len(arr), 1)) - 1
    mdd = max_drawdown(arr)
    return float(ann_return / np.maximum(mdd / 100, 1e-10))


# ──────────────────────────────────────────────
# Correlation & Beta
# ──────────────────────────────────────────────

def rolling_corr(x: NumericArray, y: NumericArray, period: int = 60) -> np.ndarray:
    """Rolling Pearson correlation."""
    sx = pd.Series(np.asarray(x))
    sy = pd.Series(np.asarray(y))
    return sx.rolling(period, min_periods=period // 2).corr(sy).values


def rolling_beta(stock_returns: NumericArray, benchmark_returns: NumericArray,
                 period: int = 60) -> np.ndarray:
    """Rolling beta: covariance(stock, benchmark) / variance(benchmark)."""
    s = pd.Series(np.asarray(stock_returns))
    b = pd.Series(np.asarray(benchmark_returns))
    cov = s.rolling(period, min_periods=period // 2).cov(b)
    var = b.rolling(period, min_periods=period // 2).var()
    return (cov / np.maximum(var, 1e-10)).values


# ──────────────────────────────────────────────
# Counting / Pattern Functions
# ──────────────────────────────────────────────

def new_high_count(close: NumericArray, period: int = 60) -> np.ndarray:
    """Rolling count of new 60-day highs."""
    arr = np.asarray(close)
    out = np.zeros(len(arr), dtype=np.int32)
    for i in range(period, len(arr)):
        window = arr[i - period + 1: i + 1]
        out[i] = int(np.sum(window == np.max(window)))
    return out


def consecutive_up_days(close: NumericArray) -> np.ndarray:
    """Consecutive up days count."""
    arr = np.asarray(close)
    returns = np.diff(arr, prepend=arr[0])
    out = np.zeros(len(arr), dtype=np.int32)
    count = 0
    for i in range(1, len(arr)):
        if returns[i] > 0:
            count += 1
        else:
            count = 0
        out[i] = count
    out[0] = 0
    return out


def volume_trend_days(volume: NumericArray, vol_ema: Optional[NumericArray] = None,
                      period: int = 20) -> np.ndarray:
    """Count of days volume > EMA(volume) in last N days."""
    v = np.asarray(volume)
    if vol_ema is None:
        vol_ema = ema(v, period)
    out = np.zeros(len(v), dtype=np.int32)
    for i in range(period, len(v)):
        out[i] = int(np.sum(v[i - period + 1: i + 1] > vol_ema[i - period + 1: i + 1]))
    return out


def ema_aligned_days(close: NumericArray, fast_period: int = 20,
                     mid_period: int = 60) -> np.ndarray:
    """Count of consecutive days EMA_fast > EMA_mid."""
    ema_f = ema(close, fast_period)
    ema_m = ema(close, mid_period)
    out = np.zeros(len(close), dtype=np.int32)
    count = 0
    for i in range(len(close)):
        if ema_f[i] > ema_m[i]:
            count += 1
        else:
            count = 0
        out[i] = count
    return out


# ──────────────────────────────────────────────
# Future Return (for ML target)
# ──────────────────────────────────────────────

def future_return(close: NumericArray, periods: int = 60) -> np.ndarray:
    """Forward return over N periods (percent)."""
    arr = np.asarray(close, dtype=np.float64)
    shifted = np.roll(arr, -periods)
    shifted[-periods:] = np.nan
    return (shifted / arr - 1) * 100
