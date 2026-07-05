# -*- coding: utf-8 -*-
"""
Indicator Engine — 基于 TA-Lib 的技术指标计算 + 信号生成

封装常用指标:
  趋势类: MA, EMA, MACD
  震荡类: KDJ, RSI, BOLL
  成交量: VOL, OBV

信号工具:
  CROSS(a, b)       — 上穿: 前一天 a<b, 今天 a>=b
  CROSS_DOWN(a, b)  — 下穿: 前一天 a>b, 今天 a<=b
  GoldenCross       — 均线金叉
  DeathCross        — 均线死叉
"""
from __future__ import annotations
from typing import Tuple, Optional

import numpy as np
import pandas as pd
import talib


# =========================================================
# 趋势类指标
# =========================================================
def MA(close: pd.Series, period: int = 5) -> pd.Series:
    """简单移动平均"""
    return pd.Series(talib.SMA(close.values, timeperiod=period),
                     index=close.index, name=f"MA{period}")


def EMA(close: pd.Series, period: int = 12) -> pd.Series:
    """指数移动平均"""
    return pd.Series(talib.EMA(close.values, timeperiod=period),
                     index=close.index, name=f"EMA{period}")


def MACD(close: pd.Series,
         fast: int = 12, slow: int = 26, signal: int = 9
         ) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """MACD 指标

    Returns:
        (DIF, DEA, MACD柱) 三元组
    """
    dif, dea, macd = talib.MACD(close.values, fastperiod=fast,
                                 slowperiod=slow, signalperiod=signal)
    return (pd.Series(dif, index=close.index, name="DIF"),
            pd.Series(dea, index=close.index, name="DEA"),
            pd.Series(macd, index=close.index, name="MACD"))


# =========================================================
# 震荡类指标
# =========================================================
def KDJ(high: pd.Series, low: pd.Series, close: pd.Series,
        n: int = 9, m1: int = 3, m2: int = 3) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """KDJ 指标 (中国标准 9,3,3)

    TA-Lib 没有原生 KDJ, 用 STOCH + SMA 实现:
      RSV = (close - LLV) / (HHV - LLV) * 100
      K = SMA(RSV, m1)
      D = SMA(K, m2)
      J = 3*K - 2*D
    """
    rsv = (close - low.rolling(n).min()) / (high.rolling(n).max() - low.rolling(n).min()) * 100
    # 用 ewm 近似 SMA(weight=1/m1)
    k = rsv.ewm(alpha=1.0 / m1, adjust=False).mean()
    d = k.ewm(alpha=1.0 / m2, adjust=False).mean()
    j = 3 * k - 2 * d
    return (k, d, j)


def RSI(close: pd.Series, period: int = 6) -> pd.Series:
    """RSI 相对强弱指标"""
    return pd.Series(talib.RSI(close.values, timeperiod=period),
                     index=close.index, name=f"RSI{period}")


def BOLL(close: pd.Series, period: int = 20, nbdev: int = 2
         ) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """布林带

    Returns:
        (UPPER, MIDDLE, LOWER)
    """
    upper, middle, lower = talib.BBANDS(close.values, timeperiod=period,
                                         nbdevup=nbdev, nbdevdn=nbdev)
    return (pd.Series(upper, index=close.index, name="BOLL_UP"),
            pd.Series(middle, index=close.index, name="BOLL_MID"),
            pd.Series(lower, index=close.index, name="BOLL_DN"))


# =========================================================
# 成交量指标
# =========================================================
def OBV(close: pd.Series, vol: pd.Series) -> pd.Series:
    """能量潮指标"""
    return pd.Series(talib.OBV(close.values, vol.values),
                     index=close.index, name="OBV")


# =========================================================
# 信号工具
# =========================================================
def CROSS(a: pd.Series, b: pd.Series) -> pd.Series:
    """上穿信号: 前一天 a<b, 今天 a>=b

    Returns:
        bool Series, True = 发生上穿
    """
    return (a.shift(1) < b.shift(1)) & (a >= b)


def CROSS_DOWN(a: pd.Series, b: pd.Series) -> pd.Series:
    """下穿信号: 前一天 a>b, 今天 a<=b"""
    return (a.shift(1) > b.shift(1)) & (a <= b)


def golden_cross(close: pd.Series, fast: int = 5, slow: int = 10) -> pd.Series:
    """均线金叉: MA(fast) 上穿 MA(slow)"""
    ma_f = MA(close, fast)
    ma_s = MA(close, slow)
    return CROSS(ma_f, ma_s)


def death_cross(close: pd.Series, fast: int = 5, slow: int = 10) -> pd.Series:
    """均线死叉: MA(fast) 下穿 MA(slow)"""
    ma_f = MA(close, fast)
    ma_s = MA(close, slow)
    return CROSS_DOWN(ma_f, ma_s)


# =========================================================
# 批量添加指标到 DataFrame
# =========================================================
def add_indicators(df: pd.DataFrame,
                   ma_periods: tuple = (5, 10, 20, 60),
                   boll_period: int = 20,
                   rsi_period: int = 6) -> pd.DataFrame:
    """一次性给 K 线 DataFrame 添加全部常用指标

    Args:
        df: 至少含 close/high/low/vol 列
    Returns:
        原 df + MA/EMA/MACD/KDJ/RSI/BOLL/OBV 列
    """
    out = df.copy()
    close, high, low, vol = out["close"], out["high"], out["low"], out["vol"]

    # 趋势
    for p in ma_periods:
        out[f"MA{p}"] = MA(close, p)
    out["EMA12"] = EMA(close, 12)
    out["EMA26"] = EMA(close, 26)
    out["DIF"], out["DEA"], out["MACD"] = MACD(close)

    # 震荡
    out["K"], out["D"], out["J"] = KDJ(high, low, close)
    out[f"RSI{rsi_period}"] = RSI(close, rsi_period)
    out["BOLL_UP"], out["BOLL_MID"], out["BOLL_DN"] = BOLL(close, boll_period)

    # 成交量
    out["OBV"] = OBV(close, vol)

    return out


if __name__ == "__main__":
    from data_loader import load_kline

    df = load_kline("000001.SZ", start_date="20240101")
    if not df.empty:
        out = add_indicators(df)
        print(f"指标列: {[c for c in out.columns if c not in df.columns]}")
        print(out[["trade_date", "close", "MA5", "MA10", "DIF", "DEA", "K", "D", "J", "RSI6"]].tail(5))
