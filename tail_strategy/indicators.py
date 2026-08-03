#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
技术指标计算模块 - 从 daily_cache 的 OHLCV 计算技术指标
输出列名与 stk_factor_pro 保持一致, 评分引擎无缝兼容:
  macd_dif_bfq, macd_dea_bfq, macd_bfq,
  kdj_bfq(J), kdj_k_bfq, kdj_d_bfq,
  rsi_bfq_6, rsi_bfq_12,
  boll_mid_bfq, boll_upper_bfq, boll_lower_bfq,
  cci_bfq, ma_bfq_5, ma_bfq_10, ma_bfq_20
"""
import numpy as np
import pandas as pd


def calc_macd(close: pd.Series, fast=12, slow=26, signal=9):
    """MACD: 返回 (dif, dea, macd柱)"""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd = (dif - dea) * 2
    return dif, dea, macd


def calc_kdj(high: pd.Series, low: pd.Series, close: pd.Series, n=9, m1=3, m2=3):
    """KDJ: 返回 (k, d, j)"""
    low_n = low.rolling(n, min_periods=1).min()
    high_n = high.rolling(n, min_periods=1).max()
    rsv = (close - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    rsv = rsv.fillna(50)
    k = rsv.ewm(com=m1 - 1, adjust=False).mean()
    d = k.ewm(com=m2 - 1, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j


def calc_rsi(close: pd.Series, n=6):
    """RSI(n)"""
    diff = close.diff()
    up = diff.clip(lower=0)
    dn = (-diff).clip(lower=0)
    ema_up = up.ewm(alpha=1 / n, adjust=False).mean()
    ema_dn = dn.ewm(alpha=1 / n, adjust=False).mean()
    rs = ema_up / ema_dn.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return rsi.fillna(50)


def calc_boll(close: pd.Series, n=20, k=2):
    """BOLL: 返回 (mid, upper, lower)"""
    mid = close.rolling(n, min_periods=1).mean()
    std = close.rolling(n, min_periods=1).std(ddof=0)
    upper = mid + k * std
    lower = mid - k * std
    return mid, upper, lower


def calc_cci(high: pd.Series, low: pd.Series, close: pd.Series, n=14):
    """CCI"""
    tp = (high + low + close) / 3
    ma = tp.rolling(n, min_periods=1).mean()
    md = (tp - ma).abs().rolling(n, min_periods=1).mean()
    cci = (tp - ma) / (md.replace(0, np.nan) * 0.015)
    return cci.fillna(0)


def enrich_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    给按 trade_date 升序排列的日线 DataFrame 附加技术指标列
    要求列: open, high, low, close, vol
    返回: 附加指标列后的 DataFrame (原df的拷贝)
    """
    if df is None or df.empty:
        return df
    out = df.copy()
    close = out['close'].astype(float)
    high = out['high'].astype(float)
    low = out['low'].astype(float)

    # MA
    out['ma_bfq_5'] = close.rolling(5, min_periods=1).mean()
    out['ma_bfq_10'] = close.rolling(10, min_periods=1).mean()
    out['ma_bfq_20'] = close.rolling(20, min_periods=1).mean()

    # MACD
    dif, dea, macd = calc_macd(close)
    out['macd_dif_bfq'] = dif
    out['macd_dea_bfq'] = dea
    out['macd_bfq'] = macd

    # KDJ
    k, d, j = calc_kdj(high, low, close)
    out['kdj_k_bfq'] = k
    out['kdj_d_bfq'] = d
    out['kdj_bfq'] = j

    # RSI
    out['rsi_bfq_6'] = calc_rsi(close, 6)
    out['rsi_bfq_12'] = calc_rsi(close, 12)

    # BOLL
    mid, upper, lower = calc_boll(close)
    out['boll_mid_bfq'] = mid
    out['boll_upper_bfq'] = upper
    out['boll_lower_bfq'] = lower

    # CCI
    out['cci_bfq'] = calc_cci(high, low, close)

    return out


def factor_row_from_daily(daily_df: pd.DataFrame, trade_date: str) -> pd.Series:
    """
    从已附加指标的日线中取指定日期的 factor_row
    daily_df: 升序排列且已 enrich_indicators
    """
    if daily_df is None or daily_df.empty:
        return None
    mask = daily_df['trade_date'].astype(str) == str(trade_date)
    if not mask.any():
        return None
    return daily_df[mask].iloc[-1]
