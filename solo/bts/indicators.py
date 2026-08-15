# -*- coding: utf-8 -*-
"""
BTS 技术指标计算（只依赖历史窗口，全部 rolling/backward，无未来数据）

说明：MA5 距离类指标天然只用当日与以前的数据；rolling().mean() 只向过去看。
"""
import numpy as np
import pandas as pd


def add_ma(df: pd.DataFrame, windows=(5, 10, 20, 60)) -> pd.DataFrame:
    """价格均线 MA5/10/20/60（含 slope 字段：当期值/前一期-1）"""
    df = df.copy()
    for n in windows:
        df[f"ma{n}"] = df["close"].rolling(n).mean()
    for n in (5, 10, 20):
        df[f"ma{n}_slope"] = df[f"ma{n}"].pct_change()
    return df


def add_vol_ma(df: pd.DataFrame, windows=(5, 10, 20)) -> pd.DataFrame:
    """量能均线 VOL_MA5/10/20"""
    df = df.copy()
    for n in windows:
        df[f"vol_ma{n}"] = df["vol"].rolling(n).mean()
    return df


def add_rsi(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    """RSI14（Wilder 平滑，仅用历史）"""
    df = df.copy()
    delta = df["close"].diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / n, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1 / n, min_periods=n).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - 100 / (1 + rs)
    return df


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """一次性附加全部指标"""
    df = add_ma(df)
    df = add_vol_ma(df)
    df = add_rsi(df)
    return df


def ma5_up_streak(ma5: pd.Series, end_idx: int) -> int:
    """截至 end_idx（含）MA5 连续向上的天数。MA5[t] > MA5[t-1] 连续计数"""
    streak = 0
    i = end_idx
    while i >= 1 and not np.isnan(ma5.iloc[i]) and not np.isnan(ma5.iloc[i - 1]) \
            and ma5.iloc[i] > ma5.iloc[i - 1]:
        streak += 1
        i -= 1
        if streak >= 60:
            break
    return streak


def ma_slope(ma: pd.Series, end_idx: int, days: int) -> float:
    """截至 end_idx 的 N 日均线斜率（ma[t]/ma[t-days]-1），需 days>=1"""
    if end_idx - days < 0:
        return np.nan
    a = ma.iloc[end_idx]
    b = ma.iloc[end_idx - days]
    if pd.isna(a) or pd.isna(b) or b == 0:
        return np.nan
    return float(a / b - 1.0)
