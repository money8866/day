"""全部TA指标自行实现（无TA-Lib依赖）。全向量化，无时间维度的for循环。

所有函数同时接受 pd.Series 和 np.ndarray，统一返回 np.ndarray (float64)。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Union, Optional, Tuple
from scipy import stats

NumericArray = Union[np.ndarray, pd.Series]

_EPS = 1e-10


# ═══════════════════════════════════════════════════════════
# 1. MOVING AVERAGES
# ═══════════════════════════════════════════════════════════

def ema(close: NumericArray, period: int = 20) -> np.ndarray:
    """Exponential Moving Average (fully vectorized)."""
    s = pd.Series(np.asarray(close, dtype=np.float64))
    return s.ewm(span=period, adjust=False).mean().values


def sma(close: NumericArray, period: int = 20) -> np.ndarray:
    """Simple Moving Average."""
    s = pd.Series(np.asarray(close, dtype=np.float64))
    return s.rolling(period, min_periods=1).mean().values


def rma(close: NumericArray, period: int = 14) -> np.ndarray:
    """Wilder's Smoothed Moving Average (RMA).

    初始值为前 period 个值的 SMA，后续使用 alpha = 1/period 的 EMA。
    通过 prepend seed 的方式实现全向量化。
    """
    alpha = 1.0 / period
    arr = np.asarray(close, dtype=np.float64)
    n = len(arr)
    out = np.full(n, np.nan)
    if n < period:
        return out
    seed = np.mean(arr[:period])
    if n > period:
        remaining = arr[period:]
        padded = np.concatenate([[seed], remaining])
        result = pd.Series(padded).ewm(alpha=alpha, adjust=False).mean().values
        out[period - 1:] = result
    else:
        out[period - 1] = seed
    return out


def _wma_weights(period: int) -> np.ndarray:
    """生成 WMA 权重数组 (最近期权重最大)。"""
    return np.arange(1, period + 1, dtype=np.float64)


def wma(close: NumericArray, period: int = 20) -> np.ndarray:
    """Weighted Moving Average (全向量化 stride-trick)。"""
    arr = np.asarray(close, dtype=np.float64)
    n = len(arr)
    out = np.full(n, np.nan)
    if n < period:
        return out
    weights = _wma_weights(period)
    weight_sum = weights.sum()
    windows = rolling_window(arr, period)
    out[period - 1:] = (windows * weights).sum(axis=1) / weight_sum
    return out


def hull_ma(close: NumericArray, period: int = 20) -> np.ndarray:
    """Hull Moving Average。"""
    half = period // 2
    sqrt_period = int(np.sqrt(period))
    wma_half = wma(close, half)
    wma_full = wma(close, period)
    raw = 2.0 * wma_half - wma_full
    return wma(raw, sqrt_period)


def dema(close: NumericArray, period: int = 20) -> np.ndarray:
    """Double Exponential Moving Average: DEMA = 2*EMA - EMA(EMA)。"""
    e1 = ema(close, period)
    e2 = ema(e1, period)
    return 2.0 * e1 - e2


def tema(close: NumericArray, period: int = 20) -> np.ndarray:
    """Triple Exponential Moving Average: TEMA = 3*EMA - 3*EMA(EMA) + EMA(EMA(EMA))。"""
    e1 = ema(close, period)
    e2 = ema(e1, period)
    e3 = ema(e2, period)
    return 3.0 * e1 - 3.0 * e2 + e3


# ═══════════════════════════════════════════════════════════
# 2. TRUE RANGE & ATR
# ═══════════════════════════════════════════════════════════

def true_range(high: NumericArray, low: NumericArray, close: NumericArray,
               prev_close: Optional[NumericArray] = None) -> np.ndarray:
    """True Range = max(high-low, |high-prev_close|, |low-prev_close|)。"""
    h, l, c = [np.asarray(x, dtype=np.float64) for x in (high, low, close)]
    if prev_close is not None:
        pc = np.asarray(prev_close, dtype=np.float64)
    else:
        pc = np.roll(c, 1)
        pc[0] = c[0]
    tr = np.maximum(h - l, np.abs(h - pc))
    tr = np.maximum(tr, np.abs(l - pc))
    return tr


def atr(high: NumericArray, low: NumericArray, close: NumericArray,
        period: int = 14) -> np.ndarray:
    """Average True Range (使用 Wilder's RMA)。"""
    tr = true_range(high, low, close)
    return rma(tr, period)


def natr(high: NumericArray, low: NumericArray, close: NumericArray,
         period: int = 14) -> np.ndarray:
    """Normalized ATR = ATR / close * 100。"""
    a = atr(high, low, close, period)
    c = np.asarray(close, dtype=np.float64)
    return a / np.maximum(c, _EPS) * 100.0


# ═══════════════════════════════════════════════════════════
# 3. ADX / DIRECTIONAL MOVEMENT
# ═══════════════════════════════════════════════════════════

def _plus_dm_raw(high: np.ndarray, low: np.ndarray) -> np.ndarray:
    """Raw +DM (向上方向运动)。"""
    h_diff = np.diff(high, prepend=high[0:1])
    l_diff = np.diff(low, prepend=low[0:1])
    up_move = np.where((h_diff > l_diff) & (h_diff > 0), h_diff, 0.0)
    return up_move


def _minus_dm_raw(high: np.ndarray, low: np.ndarray) -> np.ndarray:
    """Raw -DM (向下方向运动)。"""
    h_diff = np.diff(high, prepend=high[0:1])
    l_diff = np.diff(low, prepend=low[0:1])
    down_move = np.where((l_diff > h_diff) & (l_diff > 0), l_diff, 0.0)
    return down_move


def plus_dm(high: NumericArray, low: NumericArray, period: int = 14) -> np.ndarray:
    """Smoothed +DM (Wilder's RMA)。"""
    h, l = [np.asarray(x, dtype=np.float64) for x in (high, low)]
    raw = _plus_dm_raw(h, l)
    return rma(raw, period)


def minus_dm(high: NumericArray, low: NumericArray, period: int = 14) -> np.ndarray:
    """Smoothed -DM (Wilder's RMA)。"""
    h, l = [np.asarray(x, dtype=np.float64) for x in (high, low)]
    raw = _minus_dm_raw(h, l)
    return rma(raw, period)


def plus_di(high: NumericArray, low: NumericArray, close: NumericArray,
            period: int = 14) -> np.ndarray:
    """+DI = 100 * RMA(+DM) / ATR。"""
    h, l, c = [np.asarray(x, dtype=np.float64) for x in (high, low, close)]
    atr_val = atr(h, l, c, period)
    raw_dm = _plus_dm_raw(h, l)
    return 100.0 * rma(raw_dm, period) / np.maximum(atr_val, _EPS)


def minus_di(high: NumericArray, low: NumericArray, close: NumericArray,
             period: int = 14) -> np.ndarray:
    """-DI = 100 * RMA(-DM) / ATR。"""
    h, l, c = [np.asarray(x, dtype=np.float64) for x in (high, low, close)]
    atr_val = atr(h, l, c, period)
    raw_dm = _minus_dm_raw(h, l)
    return 100.0 * rma(raw_dm, period) / np.maximum(atr_val, _EPS)


def adx(high: NumericArray, low: NumericArray, close: NumericArray,
        period: int = 14) -> np.ndarray:
    """Average Directional Index。"""
    h, l, c = [np.asarray(x, dtype=np.float64) for x in (high, low, close)]
    pdi = plus_di(h, l, c, period)
    mdi = minus_di(h, l, c, period)
    dx = 100.0 * np.abs(pdi - mdi) / np.maximum(pdi + mdi, _EPS)
    return rma(dx, period)


def adxr(high: NumericArray, low: NumericArray, close: NumericArray,
         period: int = 14) -> np.ndarray:
    """ADX Rating: (ADX[i] + ADX[i-period]) / 2。"""
    adx_val = adx(high, low, close, period)
    shifted = np.roll(adx_val, period)
    shifted[:period] = np.nan
    return (adx_val + shifted) / 2.0


# ═══════════════════════════════════════════════════════════
# 4. MOMENTUM
# ═══════════════════════════════════════════════════════════

def rsi(close: NumericArray, period: int = 14) -> np.ndarray:
    """Relative Strength Index (使用 Wilder's RMA)。"""
    arr = np.asarray(close, dtype=np.float64)
    delta = np.diff(arr, prepend=arr[0:1])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = rma(gain, period)
    avg_loss = rma(loss, period)
    rs = avg_gain / np.maximum(avg_loss, _EPS)
    return 100.0 - 100.0 / (1.0 + rs)


def macd(close: NumericArray, fast: int = 12, slow: int = 26,
         signal: int = 9) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """MACD: (MACD线, 信号线, 柱状线)。"""
    ema_f = ema(close, fast)
    ema_s = ema(close, slow)
    macd_line = ema_f - ema_s
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def macd_signal(close: NumericArray, fast: int = 12, slow: int = 26,
                signal: int = 9) -> np.ndarray:
    """MACD 信号线 (DEA)。"""
    return macd(close, fast, slow, signal)[1]


def macd_hist(close: NumericArray, fast: int = 12, slow: int = 26,
              signal: int = 9) -> np.ndarray:
    """MACD 柱状线 (MACD - 信号线)。"""
    return macd(close, fast, slow, signal)[2]


def roc(close: NumericArray, period: int = 12) -> np.ndarray:
    """Rate of Change (%): (close / close.shift(period) - 1) * 100。"""
    arr = np.asarray(close, dtype=np.float64)
    shifted = np.roll(arr, period)
    shifted[:period] = np.nan
    return (arr / np.maximum(shifted, _EPS) - 1.0) * 100.0


def rocp(close: NumericArray, period: int = 12) -> np.ndarray:
    """Rate of Change (decimal): close / close.shift(period) - 1。"""
    arr = np.asarray(close, dtype=np.float64)
    shifted = np.roll(arr, period)
    shifted[:period] = np.nan
    return arr / np.maximum(shifted, _EPS) - 1.0


def mom(close: NumericArray, period: int = 10) -> np.ndarray:
    """Momentum: close - close.shift(period)。"""
    arr = np.asarray(close, dtype=np.float64)
    shifted = np.roll(arr, period)
    shifted[:period] = np.nan
    return arr - shifted


def williams_r(high: NumericArray, low: NumericArray, close: NumericArray,
               period: int = 14) -> np.ndarray:
    """Williams %R。"""
    h, l, c = [np.asarray(x, dtype=np.float64) for x in (high, low, close)]
    hh = pd.Series(h).rolling(period, min_periods=1).max().values
    ll = pd.Series(l).rolling(period, min_periods=1).min().values
    return (hh - c) / np.maximum(hh - ll, _EPS) * (-100.0)


def cci(high: NumericArray, low: NumericArray, close: NumericArray,
        period: int = 14) -> np.ndarray:
    """Commodity Channel Index。"""
    h, l, c = [np.asarray(x, dtype=np.float64) for x in (high, low, close)]
    tp = (h + l + c) / 3.0
    tp_series = pd.Series(tp)
    tp_sma = tp_series.rolling(period, min_periods=1).mean().values
    tp_mad = tp_series.rolling(period, min_periods=1).apply(
        lambda x: np.mean(np.abs(x - np.mean(x))), raw=True
    ).values
    return (tp - tp_sma) / np.maximum(tp_mad * 0.015, _EPS)


def tsi(close: NumericArray, fast: int = 13, slow: int = 25) -> np.ndarray:
    """True Strength Index。"""
    arr = np.asarray(close, dtype=np.float64)
    delta = np.diff(arr, prepend=arr[0:1])
    abs_delta = np.abs(delta)
    ema_slow_delta = ema(ema(delta, slow), fast)
    ema_slow_abs = ema(ema(abs_delta, slow), fast)
    return 100.0 * ema_slow_delta / np.maximum(ema_slow_abs, _EPS)


def uo(high: NumericArray, low: NumericArray, close: NumericArray,
       period1: int = 7, period2: int = 14, period3: int = 28) -> np.ndarray:
    """Ultimate Oscillator。"""
    h, l, c = [np.asarray(x, dtype=np.float64) for x in (high, low, close)]
    pc = np.roll(c, 1)
    pc[0] = c[0]

    bp = c - np.minimum(l, pc)
    tr = np.maximum(h, pc) - np.minimum(l, pc)

    sum_bp1 = pd.Series(bp).rolling(period1, min_periods=1).sum().values
    sum_tr1 = pd.Series(tr).rolling(period1, min_periods=1).sum().values
    sum_bp2 = pd.Series(bp).rolling(period2, min_periods=1).sum().values
    sum_tr2 = pd.Series(tr).rolling(period2, min_periods=1).sum().values
    sum_bp3 = pd.Series(bp).rolling(period3, min_periods=1).sum().values
    sum_tr3 = pd.Series(tr).rolling(period3, min_periods=1).sum().values

    avg1 = sum_bp1 / np.maximum(sum_tr1, _EPS)
    avg2 = sum_bp2 / np.maximum(sum_tr2, _EPS)
    avg3 = sum_bp3 / np.maximum(sum_tr3, _EPS)

    return 100.0 * (4.0 * avg1 + 2.0 * avg2 + avg3) / 7.0


# ═══════════════════════════════════════════════════════════
# 5. BOLLINGER BANDS
# ═══════════════════════════════════════════════════════════

def bollinger(close: NumericArray, period: int = 20,
              nbdev: float = 2) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Bollinger Bands: (上轨, 中轨, 下轨)。"""
    s = pd.Series(np.asarray(close, dtype=np.float64))
    middle = s.rolling(period, min_periods=1).mean().values
    std = s.rolling(period, min_periods=1).std(ddof=0).values
    upper = middle + nbdev * std
    lower = middle - nbdev * std
    return upper, middle, lower


def bollinger_width(close: NumericArray, period: int = 20,
                    nbdev: float = 2) -> np.ndarray:
    """Bollinger Band Width: (上轨 - 下轨) / 中轨。"""
    upper, middle, _ = bollinger(close, period, nbdev)
    return (upper - middle) * 2.0 / np.maximum(middle, _EPS)


def bollinger_pct(close: NumericArray, period: int = 20,
                  nbdev: float = 2) -> np.ndarray:
    """Bollinger %B: (close - 下轨) / (上轨 - 下轨)。"""
    c = np.asarray(close, dtype=np.float64)
    upper, middle, lower = bollinger(close, period, nbdev)
    return (c - lower) / np.maximum(upper - lower, _EPS)


# ═══════════════════════════════════════════════════════════
# 6. KDJ / STOCHASTIC
# ═══════════════════════════════════════════════════════════

def kdj(high: NumericArray, low: NumericArray, close: NumericArray,
        period: int = 9, k_smooth: int = 3, d_smooth: int = 3
        ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """KDJ 指标: (K, D, J)。"""
    h, l, c = [np.asarray(x, dtype=np.float64) for x in (high, low, close)]
    hh = pd.Series(h).rolling(period, min_periods=1).max().values
    ll = pd.Series(l).rolling(period, min_periods=1).min().values
    rsv = (c - ll) / np.maximum(hh - ll, _EPS) * 100.0

    k = ema(rsv, k_smooth)
    d = ema(k, d_smooth)
    j = 3.0 * k - 2.0 * d
    return k, d, j


def stoch(high: NumericArray, low: NumericArray, close: NumericArray,
          fastk: int = 14, slowk: int = 3, slowd: int = 3
          ) -> Tuple[np.ndarray, np.ndarray]:
    """Stochastic Oscillator: (slow %K, slow %D)。"""
    h, l, c = [np.asarray(x, dtype=np.float64) for x in (high, low, close)]
    hh = pd.Series(h).rolling(fastk, min_periods=1).max().values
    ll = pd.Series(l).rolling(fastk, min_periods=1).min().values
    fastk_line = (c - ll) / np.maximum(hh - ll, _EPS) * 100.0

    slowk_line = sma(fastk_line, slowk)
    slowd_line = sma(slowk_line, slowd)
    return slowk_line, slowd_line


def stochrsi(close: NumericArray, period: int = 14, fastk: int = 3,
             fastd: int = 3) -> Tuple[np.ndarray, np.ndarray]:
    """Stochastic RSI: (StochRSI %K, StochRSI %D)。"""
    rsi_val = rsi(close, period)
    # 先对 RSI 进行滚动归一化
    s = pd.Series(rsi_val)
    hh = s.rolling(period, min_periods=1).max().values
    ll = s.rolling(period, min_periods=1).min().values
    stoch_rsi = (rsi_val - ll) / np.maximum(hh - ll, _EPS) * 100.0

    k = sma(stoch_rsi, fastk)
    d = sma(k, fastd)
    return k, d


# ═══════════════════════════════════════════════════════════
# 7. VOLUME INDICATORS
# ═══════════════════════════════════════════════════════════

def obv(close: NumericArray, volume: NumericArray) -> np.ndarray:
    """On-Balance Volume。"""
    c = np.asarray(close, dtype=np.float64)
    v = np.asarray(volume, dtype=np.float64)
    direction = np.where(c[1:] > c[:-1], 1.0, np.where(c[1:] < c[:-1], -1.0, 0.0))
    signed_vol = np.concatenate([[0.0], direction * v[1:]])
    return np.cumsum(signed_vol)


def obv_ema(close: NumericArray, volume: NumericArray, period: int = 20) -> np.ndarray:
    """OBV 的 EMA。"""
    return ema(obv(close, volume), period)


def volume_ratio(volume: NumericArray, vol_ema_period: int = 20) -> np.ndarray:
    """量比 = volume / EMA(volume)。"""
    v = np.asarray(volume, dtype=np.float64)
    vol_ema_val = ema(v, vol_ema_period)
    return v / np.maximum(vol_ema_val, _EPS)


def ad_line(high: NumericArray, low: NumericArray, close: NumericArray,
            volume: NumericArray) -> np.ndarray:
    """A/D Line (Accumulation/Distribution Line)。"""
    h, l, c, v = [np.asarray(x, dtype=np.float64) for x in (high, low, close, volume)]
    mfm = ((c - l) - (h - c)) / np.maximum(h - l, _EPS)
    mfv = mfm * v
    return np.cumsum(mfv)


def adosc(high: NumericArray, low: NumericArray, close: NumericArray,
          volume: NumericArray, fast: int = 3, slow: int = 10) -> np.ndarray:
    """A/D Oscillator = EMA(ad, fast) - EMA(ad, slow)。"""
    ad = ad_line(high, low, close, volume)
    return ema(ad, fast) - ema(ad, slow)


def mfi(high: NumericArray, low: NumericArray, close: NumericArray,
        volume: NumericArray, period: int = 14) -> np.ndarray:
    """Money Flow Index。"""
    h, l, c, v = [np.asarray(x, dtype=np.float64) for x in (high, low, close, volume)]
    tp = (h + l + c) / 3.0
    tp_prev = np.roll(tp, 1)
    tp_prev[0] = tp[0]

    pos_flow = np.where(tp > tp_prev, tp * v, 0.0)
    neg_flow = np.where(tp < tp_prev, tp * v, 0.0)

    pos_sum = pd.Series(pos_flow).rolling(period, min_periods=1).sum().values
    neg_sum = pd.Series(neg_flow).rolling(period, min_periods=1).sum().values

    mfr = pos_sum / np.maximum(neg_sum, _EPS)
    return 100.0 - 100.0 / (1.0 + mfr)


# ═══════════════════════════════════════════════════════════
# 8. VOLATILITY
# ═══════════════════════════════════════════════════════════

def stddev(close: NumericArray, period: int = 20, ddof: int = 0) -> np.ndarray:
    """Rolling standard deviation。"""
    return pd.Series(np.asarray(close, dtype=np.float64)).rolling(
        period, min_periods=1
    ).std(ddof=ddof).values


def variance(close: NumericArray, period: int = 20, ddof: int = 0) -> np.ndarray:
    """Rolling variance。"""
    return pd.Series(np.asarray(close, dtype=np.float64)).rolling(
        period, min_periods=1
    ).var(ddof=ddof).values


def beta(stock_returns: NumericArray, benchmark_returns: NumericArray,
         period: int = 60) -> np.ndarray:
    """Rolling beta = Cov(s, b) / Var(b)。"""
    s = pd.Series(np.asarray(stock_returns, dtype=np.float64))
    b = pd.Series(np.asarray(benchmark_returns, dtype=np.float64))
    cov = s.rolling(period, min_periods=period // 2).cov(b)
    var = b.rolling(period, min_periods=period // 2).var()
    return (cov / np.maximum(var, _EPS)).values


# ═══════════════════════════════════════════════════════════
# 9. PATTERN RECOGNITION
# ═══════════════════════════════════════════════════════════

def slope(close: NumericArray, period: int = 14) -> np.ndarray:
    """Linear regression slope over rolling window (percent per day)。

    全向量化实现，使用 stride-trick 滚动窗口 + 向量化回归公式。
    """
    arr = np.asarray(close, dtype=np.float64)
    n = len(arr)
    out = np.full(n, np.nan)
    if n < period:
        return out
    x = np.arange(period, dtype=np.float64)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()
    windows = rolling_window(arr, period)
    y_mean = windows.mean(axis=1)
    cov = ((x - x_mean) * (windows - y_mean[:, None])).sum(axis=1)
    slope_vals = cov / x_var
    out[period - 1:] = slope_vals / np.maximum(y_mean, _EPS) * 100.0
    return out


def linear_reg(close: NumericArray, period: int = 14) -> np.ndarray:
    """Linear regression intercept over rolling window。"""
    arr = np.asarray(close, dtype=np.float64)
    n = len(arr)
    out = np.full(n, np.nan)
    if n < period:
        return out
    x = np.arange(period, dtype=np.float64)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()
    windows = rolling_window(arr, period)
    y_mean = windows.mean(axis=1)
    cov = ((x - x_mean) * (windows - y_mean[:, None])).sum(axis=1)
    slope_vals = cov / x_var
    out[period - 1:] = y_mean - slope_vals * x_mean
    return out


def linear_reg_angle(close: NumericArray, period: int = 14) -> np.ndarray:
    """Linear regression angle (degrees)。"""
    arr = np.asarray(close, dtype=np.float64)
    n = len(arr)
    out = np.full(n, np.nan)
    if n < period:
        return out
    x = np.arange(period, dtype=np.float64)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()
    windows = rolling_window(arr, period)
    y_mean = windows.mean(axis=1)
    cov = ((x - x_mean) * (windows - y_mean[:, None])).sum(axis=1)
    slope_vals = cov / x_var
    out[period - 1:] = np.degrees(np.arctan(slope_vals))
    return out


def hurst_exponent(close: NumericArray, max_lag: int = 20) -> float:
    """Hurst Exponent: >0.5 趋势, <0.5 均值回归。"""
    arr = np.asarray(close, dtype=np.float64)
    arr = arr[~np.isnan(arr)]
    if len(arr) < max_lag * 2:
        return 0.5
    lags = np.arange(2, max_lag, dtype=np.float64)
    tau = np.array([np.std(np.diff(arr, int(lag))) for lag in lags])
    if np.any(tau == 0):
        return 0.5
    poly = np.polyfit(np.log(lags), np.log(tau), 1)
    return float(poly[0] / 2.0)


# ═══════════════════════════════════════════════════════════
# 10. STATISTICAL FUNCTIONS
# ═══════════════════════════════════════════════════════════

def rolling_window(arr: np.ndarray, window: int) -> np.ndarray:
    """高效的 numpy stride 滚动窗口视图（只读）。"""
    if len(arr) < window or window < 1:
        return np.empty((0, window))
    shape = (len(arr) - window + 1, window)
    strides = (arr.strides[0], arr.strides[0])
    return np.lib.stride_tricks.as_strided(arr, shape=shape, strides=strides)


def rank_score(arr: NumericArray, period: int = 60) -> np.ndarray:
    """滚动百分位排名 (0-100)。"""
    s = pd.Series(np.asarray(arr))
    rank = s.rolling(period, min_periods=1).apply(
        lambda x: stats.percentileofscore(x, x[-1]) if len(x) > 0 else 50.0,
        raw=True
    )
    return rank.values


def normalize(arr: NumericArray) -> np.ndarray:
    """Min-Max 归一化到 [0, 1]。"""
    a = np.asarray(arr, dtype=np.float64)
    mn, mx = np.nanmin(a), np.nanmax(a)
    if mx == mn:
        return np.full_like(a, 0.5)
    return (a - mn) / (mx - mn)


def zscore(arr: NumericArray) -> np.ndarray:
    """Z-Score 标准化。"""
    a = np.asarray(arr, dtype=np.float64)
    mu, sigma = np.nanmean(a), np.nanstd(a)
    if sigma == 0 or np.isnan(sigma):
        return np.zeros_like(a)
    return (a - mu) / sigma


def winsorize(arr: NumericArray, limits: float = 0.01) -> np.ndarray:
    """Winsorize 截尾处理。"""
    a = np.asarray(arr, dtype=np.float64)
    valid = a[~np.isnan(a)]
    if len(valid) == 0:
        return a
    lower = np.percentile(valid, limits * 100.0)
    upper = np.percentile(valid, (1.0 - limits) * 100.0)
    return np.clip(a, lower, upper)


# ═══════════════════════════════════════════════════════════
# 11. PERFORMANCE METRICS
# ═══════════════════════════════════════════════════════════

def max_drawdown(close: NumericArray) -> float:
    """最大回撤 (正百分比)。"""
    arr = np.asarray(close, dtype=np.float64)
    peak = np.maximum.accumulate(arr)
    dd = (arr - peak) / np.maximum(peak, _EPS)
    return float(np.abs(np.nanmin(dd)) * 100.0)


def max_drawdown_series(close: NumericArray) -> np.ndarray:
    """每日回撤序列 (%)。"""
    arr = np.asarray(close, dtype=np.float64)
    peak = np.maximum.accumulate(arr)
    dd = (arr - peak) / np.maximum(peak, _EPS)
    return dd * 100.0


def sharpe_ratio(returns: NumericArray, periods_per_year: int = 252) -> float:
    """年化 Sharpe Ratio。"""
    r = np.asarray(returns, dtype=np.float64)
    r = r[~np.isnan(r)]
    if len(r) < 2:
        return 0.0
    return float(np.mean(r) / np.maximum(np.std(r, ddof=1), _EPS) * np.sqrt(periods_per_year))


def sortino_ratio(returns: NumericArray, periods_per_year: int = 252) -> float:
    """Sortino Ratio（仅考虑下行波动）。"""
    r = np.asarray(returns, dtype=np.float64)
    r = r[~np.isnan(r)]
    if len(r) < 2:
        return 0.0
    downside = r[r < 0]
    if len(downside) < 1:
        return float(np.mean(r) * np.sqrt(periods_per_year) / _EPS)
    downside_std = np.std(downside, ddof=1)
    return float(np.mean(r) / np.maximum(downside_std, _EPS) * np.sqrt(periods_per_year))


def calmar_ratio(close: NumericArray, periods_per_year: int = 252) -> float:
    """Calmar Ratio = 年化收益率 / 最大回撤。"""
    arr = np.asarray(close, dtype=np.float64)
    total_return = (arr[-1] / np.maximum(arr[0], _EPS)) - 1.0
    n = max(len(arr), 1)
    ann_return = (1.0 + total_return) ** (periods_per_year / n) - 1.0
    mdd = max_drawdown(arr) / 100.0
    return float(ann_return / np.maximum(mdd, _EPS))


def rolling_sharpe(returns: NumericArray, period: int = 60,
                   periods_per_year: int = 252) -> np.ndarray:
    """滚动年化 Sharpe Ratio。"""
    r = pd.Series(np.asarray(returns, dtype=np.float64))
    mean = r.rolling(period, min_periods=period // 2).mean()
    std = r.rolling(period, min_periods=period // 2).std(ddof=1)
    return (mean / np.maximum(std, _EPS) * np.sqrt(periods_per_year)).values


# ═══════════════════════════════════════════════════════════
# 12. CORRELATION
# ═══════════════════════════════════════════════════════════

def rolling_corr(x: NumericArray, y: NumericArray, period: int = 60) -> np.ndarray:
    """滚动 Pearson 相关系数。"""
    sx = pd.Series(np.asarray(x, dtype=np.float64))
    sy = pd.Series(np.asarray(y, dtype=np.float64))
    return sx.rolling(period, min_periods=period // 2).corr(sy).values


def rolling_cov(x: NumericArray, y: NumericArray, period: int = 60) -> np.ndarray:
    """滚动协方差。"""
    sx = pd.Series(np.asarray(x, dtype=np.float64))
    sy = pd.Series(np.asarray(y, dtype=np.float64))
    return sx.rolling(period, min_periods=period // 2).cov(sy).values


# ═══════════════════════════════════════════════════════════
# 13. COUNTING FUNCTIONS
# ═══════════════════════════════════════════════════════════

def new_high_count(close: NumericArray, period: int = 60) -> np.ndarray:
    """滚动窗口中创 N 日新高的次数。

    对每个窗口统计最大值在窗口中出现的频次。
    主计算使用 stride-trick 全向量化，仅前 period-1 个数据用短循环填充。
    """
    arr = np.asarray(close, dtype=np.float64)
    n = len(arr)
    out = np.zeros(n, dtype=np.int32)
    if n < 2:
        return out
    # 前 period-1 个位置用扩展窗口
    bound = min(period - 1, n)
    for i in range(bound):
        out[i] = int(np.sum(arr[:i + 1] == np.max(arr[:i + 1])))
    # 主体部分：stride-trick 滚动窗口
    if n >= period:
        windows = rolling_window(arr, period)
        max_vals = np.max(windows, axis=1, keepdims=True)
        out[period - 1:] = np.sum(windows == max_vals, axis=1).astype(np.int32)
    return out


def _consecutive_count(condition: np.ndarray) -> np.ndarray:
    """辅助函数：计算 bool 数组中连续 True 的累积天数。

    使用 pandas groupby cumcount 实现全向量化。
    """
    s = pd.Series(condition.astype(int))
    groups = (~condition).cumsum()
    result = s.groupby(groups).cumcount() + 1
    return result.where(condition, 0).values.astype(np.int32)


def consecutive_up_days(close: NumericArray) -> np.ndarray:
    """连续上涨天数。"""
    arr = np.asarray(close, dtype=np.float64)
    direction = np.diff(arr, prepend=arr[0:1]) > 0
    return _consecutive_count(direction)


def consecutive_down_days(close: NumericArray) -> np.ndarray:
    """连续下跌天数。"""
    arr = np.asarray(close, dtype=np.float64)
    direction = np.diff(arr, prepend=arr[0:1]) < 0
    return _consecutive_count(direction)


def volume_trend_days(volume: NumericArray,
                      vol_ema: Optional[NumericArray] = None,
                      period: int = 20) -> np.ndarray:
    """过去 N 日中成交量大于其 EMA 的天数。"""
    v = np.asarray(volume, dtype=np.float64)
    if vol_ema is None:
        vol_ema_val = ema(v, period)
    else:
        vol_ema_val = np.asarray(vol_ema, dtype=np.float64)
    condition = v > vol_ema_val
    return pd.Series(condition.astype(float)).rolling(
        period, min_periods=1
    ).sum().values.astype(np.int32)


def ema_aligned_days(close: NumericArray, fast_period: int = 20,
                     mid_period: int = 60) -> np.ndarray:
    """EMA_fast > EMA_mid 的连续天数。"""
    ema_f = ema(close, fast_period)
    ema_m = ema(close, mid_period)
    condition = ema_f > ema_m
    return _consecutive_count(condition)


def above_ema_days(close: NumericArray, period: int = 20) -> np.ndarray:
    """收盘价在 EMA 之上的连续天数。"""
    c = np.asarray(close, dtype=np.float64)
    ema_val = ema(c, period)
    condition = c > ema_val
    return _consecutive_count(condition)


# ═══════════════════════════════════════════════════════════
# 14. FUTURE RETURN (for ML labeling)
# ═══════════════════════════════════════════════════════════

def future_return(close: NumericArray, periods: int = 60) -> np.ndarray:
    """未来 N 期的前向收益率 (%)。用于 ML 标注。"""
    arr = np.asarray(close, dtype=np.float64)
    shifted = np.roll(arr, -periods)
    shifted[-periods:] = np.nan
    return (shifted / np.maximum(arr, _EPS) - 1.0) * 100.0


# ═══════════════════════════════════════════════════════════
# 15. ADDITIONAL HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════

def kama(close: NumericArray, period: int = 10, fast: int = 2,
         slow: int = 30) -> np.ndarray:
    """Kaufman Adaptive Moving Average (KAMA)。"""
    arr = np.asarray(close, dtype=np.float64)
    n = len(arr)
    out = np.full(n, np.nan)
    if n < period:
        return out

    fastest = 2.0 / (fast + 1.0)
    slowest = 2.0 / (slow + 1.0)

    # 初始化：用 SMA 填充前 period 个值
    out[:period] = np.mean(arr[:period])
    for i in range(period, n):
        change = np.abs(arr[i] - arr[i - period])
        volatility = np.sum(np.abs(np.diff(arr[i - period + 1: i + 1])))
        er = change / np.maximum(volatility, _EPS)
        sc = (er * (fastest - slowest) + slowest) ** 2
        out[i] = out[i - 1] + sc * (arr[i] - out[i - 1])
    return out


def alma(close: NumericArray, period: int = 10, offset: float = 0.85,
         sigma: float = 6) -> np.ndarray:
    """Arnaud Legoux Moving Average (ALMA)。"""
    arr = np.asarray(close, dtype=np.float64)
    n = len(arr)
    out = np.full(n, np.nan)
    if n < period:
        return out

    m = offset * (period - 1)
    s = period / sigma
    i_vals = np.arange(period, dtype=np.float64)
    weights = np.exp(-((i_vals - m) ** 2) / (2.0 * s * s))
    weights /= np.sum(weights)

    windows = rolling_window(arr, period)
    out[period - 1:] = (windows * weights).sum(axis=1)
    return out


def vwma(close: NumericArray, volume: NumericArray, period: int = 20) -> np.ndarray:
    """Volume Weighted Moving Average (VWMA)。"""
    c = np.asarray(close, dtype=np.float64)
    v = np.asarray(volume, dtype=np.float64)
    pv = c * v
    pv_sum = pd.Series(pv).rolling(period, min_periods=1).sum().values
    v_sum = pd.Series(v).rolling(period, min_periods=1).sum().values
    return pv_sum / np.maximum(v_sum, _EPS)


def trima(close: NumericArray, period: int = 20) -> np.ndarray:
    """Triangular Moving Average (TRIMA)。

    先对前半周期做 SMA，再对后半周期做 SMA。
    """
    arr = np.asarray(close, dtype=np.float64)
    half = int(np.ceil(period / 2.0))
    sma_half = sma(arr, half)
    second_half = period - half + 1
    return sma(sma_half, second_half)
