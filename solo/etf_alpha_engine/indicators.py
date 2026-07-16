#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ETF Alpha Engine - 技术指标工具模块
====================================
所有指标函数纯向量化实现，独立可复用、可单元测试。
每个函数都有明确的输入输出契约，便于参数优化。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional, Tuple


# ============================================================
#  移动均线
# ============================================================

def sma(close: np.ndarray, period: int) -> np.ndarray:
    """简单移动平均"""
    close = np.asarray(close, dtype=np.float64)
    if len(close) < period:
        return np.full_like(close, np.nan)
    return pd.Series(close).rolling(period, min_periods=period).mean().values


def ema(close: np.ndarray, period: int) -> np.ndarray:
    """指数移动平均"""
    close = np.asarray(close, dtype=np.float64)
    return pd.Series(close).ewm(span=period, adjust=False).mean().values


def rma(close: np.ndarray, period: int) -> np.ndarray:
    """Wilder RMA"""
    close = np.asarray(close, dtype=np.float64)
    return pd.Series(close).ewm(alpha=1.0 / period, adjust=False).mean().values


# ============================================================
#  动量与相对强度
# ============================================================

def roc(close: np.ndarray, period: int) -> np.ndarray:
    """Rate of Change"""
    close = np.asarray(close, dtype=np.float64)
    out = np.full_like(close, np.nan)
    if len(close) <= period:
        return out
    out[period:] = close[period:] / np.maximum(close[:-period], 1e-10) - 1.0
    return out


def returns(close: np.ndarray) -> np.ndarray:
    """日收益率"""
    close = np.asarray(close, dtype=np.float64)
    out = np.zeros_like(close)
    out[1:] = close[1:] / np.maximum(close[:-1], 1e-10) - 1.0
    return out


def relative_strength(close: np.ndarray, benchmark: np.ndarray, period: int) -> float:
    """相对强度 = 标的收益 / 基准收益"""
    close = np.asarray(close, dtype=np.float64)
    benchmark = np.asarray(benchmark, dtype=np.float64)
    if len(close) <= period or len(benchmark) <= period:
        return 0.0
    r_etf = close[-1] / np.maximum(close[-period - 1], 1e-10) - 1.0
    r_bench = benchmark[-1] / np.maximum(benchmark[-period - 1], 1e-10) - 1.0
    if abs(r_bench) < 1e-10:
        return float(r_etf * 100)
    return float((r_etf - r_bench) * 100)


# ============================================================
#  波动率与风险
# ============================================================

def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """ATR"""
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    n = len(close)
    if n < 2:
        return np.full(n, np.nan)
    prev_close = np.concatenate([[close[0]], close[:-1]])
    tr = np.maximum.reduce([
        high - low,
        np.abs(high - prev_close),
        np.abs(low - prev_close),
    ])
    return rma(tr, period)


def natr(high, low, close, period=14) -> np.ndarray:
    """归一化ATR (百分比)"""
    a = atr(high, low, close, period)
    return a / np.maximum(close, 1e-10) * 100.0


def volatility(close: np.ndarray, period: int = 20, annualize: bool = True) -> float:
    """波动率（年化默认）"""
    close = np.asarray(close, dtype=np.float64)
    rets = returns(close)
    if len(rets) < period:
        return 0.0
    sigma = float(np.std(rets[-period:]))
    return sigma * np.sqrt(252) if annualize else sigma


def max_drawdown(close: np.ndarray) -> float:
    """最大回撤（正值）"""
    close = np.asarray(close, dtype=np.float64)
    if len(close) < 2:
        return 0.0
    running_max = np.maximum.accumulate(close)
    dd = (close - running_max) / np.maximum(running_max, 1e-10)
    return float(-np.min(dd))


def ulcer_index(close: np.ndarray, period: int = 14) -> float:
    """Ulcer Index - 衡量回撤深度和持续时间"""
    close = np.asarray(close, dtype=np.float64)
    if len(close) < period:
        period = len(close)
    if period < 2:
        return 0.0
    window = close[-period:]
    running_max = np.maximum.accumulate(window)
    dd_pct = (window - running_max) / np.maximum(running_max, 1e-10) * 100.0
    return float(np.sqrt(np.mean(dd_pct ** 2)))


# ============================================================
#  趋势质量
# ============================================================

def adx(high, low, close, period=14) -> float:
    """ADX（最新值）"""
    a = _adx_series(high, low, close, period)
    valid = a[np.isfinite(a)]
    return float(valid[-1]) if len(valid) > 0 else 0.0


def _adx_series(high, low, close, period=14) -> np.ndarray:
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    n = len(close)
    if n < period * 2:
        return np.full(n, np.nan)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    tr = np.zeros(n)
    for i in range(1, n):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = -down if down > up and down > 0 else 0.0
        pc = close[i - 1]
        tr[i] = max(high[i] - low[i], abs(high[i] - pc), abs(low[i] - pc))
    atr_s = rma(tr, period)
    plus_di = 100.0 * rma(plus_dm, period) / np.maximum(atr_s, 1e-10)
    minus_di = 100.0 * rma(minus_dm, period) / np.maximum(atr_s, 1e-10)
    dx = 100.0 * np.abs(plus_di - minus_di) / np.maximum(plus_di + minus_di, 1e-10)
    return rma(dx, period)


def rsi(close: np.ndarray, period: int = 14) -> float:
    """RSI（最新值）"""
    close = np.asarray(close, dtype=np.float64)
    n = len(close)
    if n < period + 1:
        return 50.0
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = rma(gain, period)[-1]
    avg_loss = rma(loss, period)[-1]
    if avg_loss < 1e-10:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - 100.0 / (1.0 + rs))


def slope(close: np.ndarray, period: int = 20) -> float:
    """线性回归斜率"""
    close = np.asarray(close, dtype=np.float64)
    if len(close) < period:
        return 0.0
    y = close[-period:]
    x = np.arange(period, dtype=np.float64)
    slope_val = np.polyfit(x, y, 1)[0]
    return float(slope_val)


def hurst_exponent(close: np.ndarray) -> float:
    """Hurst指数（简化R/S法）"""
    close = np.asarray(close, dtype=np.float64)
    n = len(close)
    if n < 20:
        return 0.5
    rets = np.diff(np.log(np.maximum(close, 1e-10)))
    n_half = n // 2
    hs = []
    for size in [n_half, n_half // 2, n_half // 4]:
        if size < 10:
            continue
        for start in range(0, len(rets) - size + 1, size):
            seg = rets[start:start + size]
            if len(seg) < 10:
                continue
            mean = np.mean(seg)
            cumdev = np.cumsum(seg - mean)
            r = np.max(cumdev) - np.min(cumdev)
            s = np.std(seg)
            if s > 1e-10:
                hs.append(np.log(r / s) / np.log(size))
    if not hs:
        return 0.5
    return float(np.clip(np.mean(hs), 0.0, 1.0))


# ============================================================
#  突破
# ============================================================

def breakout_pct(close: np.ndarray, high: np.ndarray, period: int) -> float:
    """当前价距N日高点的百分比（负值=距离高点）"""
    close = np.asarray(close, dtype=np.float64)
    high = np.asarray(high, dtype=np.float64)
    if len(high) < period:
        return 0.0
    hh = np.max(high[-period:])
    if hh <= 0:
        return 0.0
    return float((close[-1] / hh - 1.0) * 100.0)


def new_high_count(close: np.ndarray, period: int = 20) -> int:
    """近N日创新高天数"""
    close = np.asarray(close, dtype=np.float64)
    if len(close) < period:
        return 0
    cum_max = np.maximum.accumulate(close)
    return int(np.sum(close[-period:] == cum_max[-period:]))


# ============================================================
#  统计归一化工具
# ============================================================

def normalize(arr: np.ndarray) -> np.ndarray:
    """Min-Max归一化到[0,1]"""
    a = np.asarray(arr, dtype=np.float64)
    valid = a[np.isfinite(a)]
    if len(valid) == 0:
        return np.full_like(a, 0.5)
    mn, mx = np.nanmin(a), np.nanmax(a)
    if mx <= mn or not np.isfinite(mx - mn):
        return np.full_like(a, 0.5)
    return (a - mn) / (mx - mn)


def percentile_rank(arr: np.ndarray) -> np.ndarray:
    """百分位排名 [0,1]"""
    a = np.asarray(arr, dtype=np.float64)
    valid_mask = np.isfinite(a)
    if valid_mask.sum() == 0:
        return np.full_like(a, 0.5)
    valid = a[valid_mask]
    sorted_valid = np.sort(valid)
    ranks = np.searchsorted(sorted_valid, a).astype(np.float64) / max(len(sorted_valid), 1)
    ranks[~valid_mask] = 0.5
    return ranks


def winsorize(arr: np.ndarray, q: float = 0.01) -> np.ndarray:
    """缩尾处理"""
    a = np.asarray(arr, dtype=np.float64)
    valid = a[np.isfinite(a)]
    if len(valid) == 0:
        return a
    lo = np.percentile(valid, q * 100)
    hi = np.percentile(valid, (1 - q) * 100)
    return np.clip(a, lo, hi)


def zscore(arr: np.ndarray) -> np.ndarray:
    """Z-score标准化"""
    a = np.asarray(arr, dtype=np.float64)
    valid = a[np.isfinite(a)]
    if len(valid) == 0:
        return np.full_like(a, 0.0)
    mu = np.mean(valid)
    sigma = np.std(valid)
    if sigma < 1e-10:
        return np.zeros_like(a)
    return (a - mu) / sigma


# ============================================================
#  风险调整收益
# ============================================================

def sharpe_ratio(close: np.ndarray, period: int = 60, rf: float = 0.03) -> float:
    """年化Sharpe"""
    close = np.asarray(close, dtype=np.float64)
    rets = returns(close)
    if len(rets) < period:
        period = len(rets)
    if period < 5:
        return 0.0
    r = rets[-period:]
    mu = np.mean(r) * 252
    sigma = np.std(r) * np.sqrt(252)
    if sigma < 1e-10:
        return 0.0
    return float((mu - rf) / sigma)


def sortino_ratio(close: np.ndarray, period: int = 60, rf: float = 0.03) -> float:
    """年化Sortino"""
    close = np.asarray(close, dtype=np.float64)
    rets = returns(close)
    if len(rets) < period:
        period = len(rets)
    if period < 5:
        return 0.0
    r = rets[-period:]
    mu = np.mean(r) * 252
    downside = r[r < 0]
    if len(downside) < 2:
        return float(mu) if mu > 0 else 0.0
    dd_sigma = np.std(downside) * np.sqrt(252)
    if dd_sigma < 1e-10:
        return 0.0
    return float((mu - rf) / dd_sigma)


def calmar_ratio(close: np.ndarray, period: int = 252) -> float:
    """Calmar比率"""
    close = np.asarray(close, dtype=np.float64)
    if len(close) < period:
        period = len(close)
    if period < 10:
        return 0.0
    window = close[-period:]
    ann_ret = (window[-1] / np.maximum(window[0], 1e-10) - 1.0) * (252.0 / period)
    mdd = max_drawdown(window)
    if mdd < 1e-6:
        return float(ann_ret * 10)
    return float(ann_ret / mdd)


def beta(asset: np.ndarray, market: np.ndarray, period: int = 60) -> float:
    """Beta系数"""
    asset = np.asarray(asset, dtype=np.float64)
    market = np.asarray(market, dtype=np.float64)
    n = min(len(asset), len(market))
    if n < period + 1:
        return 1.0
    a_ret = returns(asset[-period - 1:])
    m_ret = returns(market[-period - 1:])
    cov = np.cov(a_ret, m_ret)
    if cov[1, 1] < 1e-10:
        return 1.0
    return float(cov[0, 1] / cov[1, 1])


def rolling_beta(asset: np.ndarray, market: np.ndarray, period: int = 60) -> float:
    """滚动Beta（最新值）"""
    return beta(asset, market, period)


def rolling_corr(a: np.ndarray, b: np.ndarray, period: int = 60) -> float:
    """滚动相关系数"""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    n = min(len(a), len(b))
    if n < period:
        period = n
    if period < 5:
        return 0.0
    return float(np.corrcoef(a[-period:], b[-period:])[0, 1])


# ============================================================
#  量能
# ============================================================

def volume_ratio(vol: np.ndarray, period: int = 20) -> float:
    """量比"""
    vol = np.asarray(vol, dtype=np.float64)
    if len(vol) < period + 1:
        return 1.0
    avg = np.mean(vol[-period - 1:-1])
    if avg < 1e-10:
        return 1.0
    return float(vol[-1] / avg)


def consecutive_up_days(pct_chg: np.ndarray) -> int:
    """连续上涨天数（从末尾向前数）"""
    pct = np.asarray(pct_chg, dtype=np.float64)
    count = 0
    for v in reversed(pct):
        if v > 0:
            count += 1
        else:
            break
    return count


def above_ema_days(close: np.ndarray, period: int) -> int:
    """价格持续站上EMA的天数"""
    close = np.asarray(close, dtype=np.float64)
    e = ema(close, period)
    above = close > e
    count = 0
    for v in reversed(above):
        if v:
            count += 1
        else:
            break
    return count


def consecutive_count(condition: np.ndarray) -> int:
    """从末尾开始连续True的天数"""
    cond = np.asarray(condition, dtype=bool)
    count = 0
    for v in reversed(cond):
        if v:
            count += 1
        else:
            break
    return count
