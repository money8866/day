# -*- coding: utf-8 -*-
"""
PBP 技术指标计算（只依赖历史窗口，全部 rolling/backward，无未来数据）
"""
import numpy as np
import pandas as pd


def add_ma(df: pd.DataFrame, windows=(5, 10, 20, 60)) -> pd.DataFrame:
    df = df.copy()
    for n in windows:
        df[f"ma{n}"] = df["close"].rolling(n).mean()
    for n in (5, 10, 20):
        df[f"ma{n}_slope"] = df[f"ma{n}"].pct_change()
    return df


def add_vol_ma(df: pd.DataFrame, windows=(5, 10, 20)) -> pd.DataFrame:
    df = df.copy()
    for n in windows:
        df[f"vol_ma{n}"] = df["vol"].rolling(n).mean()
    return df


def add_atr(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """ATR20（Wilder 平滑，仅用历史）"""
    df = df.copy()
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df[f"atr{n}"] = tr.ewm(alpha=1.0 / n, min_periods=n).mean()
    return df


def close_location(row) -> float:
    """CloseLocation = (Close - Low) / (High - Low)，退化时按阴阳判定"""
    rng = float(row["high"]) - float(row["low"])
    if rng > 0:
        return (float(row["close"]) - float(row["low"])) / rng
    return 1.0 if float(row["close"]) >= float(row["open"]) else 0.0


def upper_shadow_ratio(row) -> float:
    rng = float(row["high"]) - float(row["low"])
    if rng <= 0:
        return 0.0
    return (float(row["high"]) - max(float(row["open"]), float(row["close"]))) / rng


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """一次性附加全部指标"""
    df = add_ma(df)
    df = add_vol_ma(df)
    df = add_atr(df, 20)
    return df
