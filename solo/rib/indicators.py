# -*- coding: utf-8 -*-
"""
技术指标计算模块

提供：MA、EMA、ATR、VWAP、成交量均线、趋势线等
所有计算均使用 numpy/pandas，避免未来函数。
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd


def ma(series: pd.Series, period: int) -> pd.Series:
    """简单移动平均。"""
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """指数移动平均。"""
    return series.ewm(span=period, adjust=False).mean()


def atr(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 20) -> pd.Series:
    """平均真实波幅。"""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=period).mean()


def vwap(high: pd.Series, low: pd.Series, close: pd.Series,
         volume: pd.Series) -> pd.Series:
    """成交量加权平均价（单日）。"""
    typical = (high + low + close) / 3
    cum_vol = volume.cumsum()
    cum_tp_vol = (typical * volume).cumsum()
    return cum_tp_vol / cum_vol.replace(0, np.nan)


def vol_ma(volume: pd.Series, period: int = 20) -> pd.Series:
    """成交量均线。"""
    return volume.rolling(window=period, min_periods=period).mean()


def close_location(high: float, low: float, close: float) -> float:
    """收盘位置：(close - low) / (high - low)。"""
    rng = high - low
    if rng <= 0:
        return 0.5
    return (close - low) / rng


def upper_shadow_ratio(open_p: float, high: float, close: float) -> float:
    """上影线比例（相对全天振幅，标准蜡烛图定义）。

    upper_shadow / (high - low) > 0.3 表示明显长上影。
    """
    rng = high - min(open_p, close)
    if rng <= 0:
        return 0.0
    upper = high - max(open_p, close)
    return upper / rng


def lower_shadow_ratio(open_p: float, low: float, close: float) -> float:
    """下影线比例。"""
    body_bottom = min(open_p, close)
    lower = body_bottom - low
    body = abs(close - open_p)
    return lower / (body + 1e-10)


def ma_slope(ma_series: pd.Series, periods: int = 5) -> pd.Series:
    """MA 斜率（年化）。"""
    return ma_series.pct_change(periods=periods) * (252 / periods)


def detect_trend_line(highs: np.ndarray, lows: np.ndarray,
                      order: int = 1) -> Tuple[np.ndarray, np.ndarray]:
    """简单线性趋势线拟合。

    Returns:
        (upper_line, lower_line) 两条趋势线
    """
    n = len(highs)
    x = np.arange(n)

    # 下降趋势线（连接高点）
    high_coeffs = np.polyfit(x, highs, min(order, n - 1))
    upper_line = np.polyval(high_coeffs, x)

    # 上升趋势线（连接低点）
    low_coeffs = np.polyfit(x, lows, min(order, n - 1))
    lower_line = np.polyval(low_coeffs, x)

    return upper_line, lower_line


def find_local_extremes(series: np.ndarray, order: int = 5) -> Tuple[np.ndarray, np.ndarray]:
    """找局部极值点。

    Args:
        series: 价格数组
        order: 左右各多少根K线比较

    Returns:
        (high_indices, low_indices)
    """
    highs_idx = []
    lows_idx = []
    n = len(series)
    for i in range(order, n - order):
        segment = series[i - order:i + order + 1]
        if series[i] == np.max(segment):
            highs_idx.append(i)
        if series[i] == np.min(segment):
            lows_idx.append(i)
    return np.array(highs_idx), np.array(lows_idx)


def compute_impulse_atr(high: float, low: float, atr_val: float) -> float:
    """计算 ImpulseATR = (High - Low) / ATR。"""
    if atr_val <= 0:
        return 0
    return (high - low) / atr_val


def compute_impulse_return(high: float, low: float) -> float:
    """计算第一波涨幅。"""
    if low <= 0:
        return 0
    return (high - low) / low


def compute_pullback_depth(impulse_high: float, base_low: float,
                           impulse_low: float) -> float:
    """计算回撤幅度。"""
    impulse_range = impulse_high - impulse_low
    if impulse_range <= 0:
        return 1.0
    return (impulse_high - base_low) / impulse_range


def compute_retain_ratio(base_low: float, impulse_low: float,
                         impulse_high: float) -> float:
    """计算涨幅保留率。"""
    impulse_range = impulse_high - impulse_low
    if impulse_range <= 0:
        return 0.0
    return (base_low - impulse_low) / impulse_range


def volume_ratio(volume: float, vol_ma_val: float) -> float:
    """计算量比。"""
    if vol_ma_val <= 0:
        return 0.0
    return volume / vol_ma_val


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """为 DataFrame 添加所有技术指标。

    要求：DataFrame 必须包含 trade_date, open, high, low, close, vol。
    """
    if df is None or df.empty:
        return df

    df = df.copy()
    df["ma5"] = ma(df["close"], 5)
    df["ma10"] = ma(df["close"], 10)
    df["ma20"] = ma(df["close"], 20)
    df["ma60"] = ma(df["close"], 60)
    df["ema12"] = ema(df["close"], 12)
    df["ema26"] = ema(df["close"], 26)
    df["atr20"] = atr(df["high"], df["low"], df["close"], 20)
    df["vol_ma20"] = vol_ma(df["vol"], 20)
    df["vol_ma60"] = vol_ma(df["vol"], 60)
    df["vwap"] = vwap(df["high"], df["low"], df["close"], df["vol"])

    # MA 斜率
    df["ma20_slope"] = ma_slope(df["ma20"], 5)
    df["ma60_slope"] = ma_slope(df["ma60"], 5)

    # 量比（逐根）
    df["vol_ratio"] = df.apply(
        lambda r: volume_ratio(r["vol"], r["vol_ma20"]), axis=1
    )

    # 收盘位置
    df["close_loc"] = df.apply(
        lambda r: close_location(r["high"], r["low"], r["close"]), axis=1
    )

    # 上影线比例
    df["upper_shadow"] = df.apply(
        lambda r: upper_shadow_ratio(r["open"], r["high"], r["close"]), axis=1
    )

    # 下影线比例
    df["lower_shadow"] = df.apply(
        lambda r: lower_shadow_ratio(r["open"], r["low"], r["close"]), axis=1
    )

    # 涨跌
    df["pct_chg"] = df["close"].pct_change() * 100
    df["return_60d"] = df["close"].pct_change(60) * 100
    df["return_120d"] = df["close"].pct_change(120) * 100

    return df
