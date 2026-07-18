#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
入场信号检测模块
================
连续技术评分系统（替代原有模式匹配方式）：
  每只股票每日都有评分0-100，不再依赖稀缺的模式信号。
  
评分维度：
  - ma_score     30分: 价格相对均线位置（MA20/MA60支撑）
  - volume_score 25分: 成交量确认（量比、放量程度）
  - momentum_score 25分: 短中期动量（5日/10日涨幅）
  - rsi_score    20分: 超买超卖状态（RSI+KDJ）
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

LOG = logging.getLogger("timing_trading.entry_signals")


def _safe(val, default=0.0):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return default
    return float(val)


# ─────────────────────────────────────────────────────────────────
# 连续技术评分
# ─────────────────────────────────────────────────────────────────

def score_ma_position(df: pd.DataFrame) -> float:
    """均线位置评分 (0-30分)

    价格在MA20附近最佳（回踩支撑），远离MA20减分。
    MA60作为辅助支撑参考。
    """
    if df.empty:
        return 0.0
    last = df.iloc[-1]
    close = _safe(last.get("close", 0))
    ma20 = _safe(last.get("ma20", 0))
    ma60 = _safe(last.get("ma60", 0))
    dist_ma20 = _safe(last.get("dist_ma20", 0))

    if ma20 <= 0 or close <= 0:
        return 15.0  # 中性分

    # 距MA20距离打分
    if -3.0 <= dist_ma20 <= 3.0:
        score = 30  # 最佳：在MA20附近
    elif -5.0 <= dist_ma20 <= 5.0:
        score = 25
    elif -8.0 <= dist_ma20 <= 8.0:
        score = 20
    elif 8.0 < dist_ma20 <= 15.0:
        score = 15  # 偏高但仍在可接受范围
    elif -12.0 <= dist_ma20 < -5.0:
        score = 10  # 跌深了
    elif dist_ma20 > 15.0:
        score = 5   # 太高了，追高风险
    else:
        score = 5

    # 如果同时靠近MA60（支撑共振），加分
    if ma60 > 0:
        dist_ma60 = (close - ma60) / ma60 * 100
        if -3.0 <= dist_ma60 <= 3.0:
            score = min(30, score + 5)

    return float(score)


def score_volume(df: pd.DataFrame) -> float:
    """成交量评分 (0-25分)

    放量上涨、缩量回调为佳。
    """
    if df.empty or len(df) < 10:
        return 12.0
    last = df.iloc[-1]
    vol_ratio = _safe(last.get("vol_ratio", 0))
    pct_chg = _safe(last.get("pct_chg", 0))

    # 量比打分
    if vol_ratio >= 2.0 and pct_chg > 0:
        score = 25  # 放量上涨
    elif vol_ratio >= 1.5 and pct_chg > 0:
        score = 22
    elif vol_ratio >= 1.3:
        score = 20
    elif vol_ratio >= 1.0:
        score = 16
    elif vol_ratio >= 0.7 and pct_chg < 0:
        score = 18  # 缩量回调（好现象）
    elif vol_ratio >= 0.5:
        score = 12
    else:
        score = 8

    # 连续缩量后放量加分
    if len(df) >= 10:
        recent_vols = df["vol"].tail(10).values
        vol_trend = recent_vols[-1] > recent_vols[:-1].mean() * 1.2
        if vol_trend:
            score = min(25, score + 3)

    return float(score)


def score_momentum(df: pd.DataFrame) -> float:
    """动量评分 (0-25分)

    短中期涨幅（5日/10日/20日）的综合动量。
    避免追高（涨幅过大减分）。
    """
    if df.empty or len(df) < 5:
        return 12.0

    closes = df["close"].values
    pct_5d = (closes[-1] / closes[-5] - 1) * 100 if len(df) >= 5 else 0
    pct_10d = (closes[-1] / closes[-10] - 1) * 100 if len(df) >= 10 else 0
    pct_20d = (closes[-1] / closes[-20] - 1) * 100 if len(df) >= 20 else 0

    score = 0

    # 5日动量（权重最高）
    if pct_5d > 3:
        score += 10
    elif pct_5d > 1:
        score += 8
    elif pct_5d > 0:
        score += 6
    elif pct_5d > -3:
        score += 4
    else:
        score += 1

    # 10日动量
    if pct_10d > 5:
        score += 8
    elif pct_10d > 2:
        score += 6
    elif pct_10d > 0:
        score += 4
    elif pct_10d > -5:
        score += 2
    else:
        score += 0

    # 20日趋势（加分项）
    if pct_20d > 8:
        score += 5
    elif pct_20d > 3:
        score += 4
    elif pct_20d > 0:
        score += 3
    else:
        score += 1

    # 涨幅过大惩罚（>30%在20日内需要回调）
    if pct_20d > 30:
        score = max(5, score - 5)
    elif pct_10d > 15:
        score = max(8, score - 3)

    return float(min(25, max(0, score)))


def score_rsi_kdj(df: pd.DataFrame) -> float:
    """超买超卖评分 (0-20分)

    RSI和KDJ结合判断，避免追高，优选回调到位的。
    """
    if df.empty:
        return 10.0

    last = df.iloc[-1]
    rsi_6 = _safe(last.get("rsi_6", 50))
    rsi_14 = _safe(last.get("rsi_12", 50))
    kdj_k = _safe(last.get("kdj_k", 50))
    kdj_j = _safe(last.get("kdj_j", 50))

    score = 10  # 中性基础分

    # RSI评分
    if rsi_14 < 30:
        score += 7  # 超卖反弹机会
    elif rsi_14 < 40:
        score += 5
    elif rsi_14 < 50:
        score += 3
    elif rsi_14 > 75:
        score -= 5  # 超买风险
    elif rsi_14 > 65:
        score -= 2

    # KDJ评分
    if kdj_j < 20:
        score += 3  # KDJ超卖
    elif kdj_j > 100:
        score -= 4  # KDJ超买

    # KDJ金叉（J上穿K）
    if len(df) >= 2:
        prev_j = _safe(df.iloc[-2].get("kdj_j", 50))
        prev_k = _safe(df.iloc[-2].get("kdj_k", 50))
        if kdj_j > kdj_k and prev_j <= prev_k:
            score += 2

    return float(min(20, max(0, score)))


def continuous_entry_score(df: pd.DataFrame, config: dict = None) -> dict:
    """连续入场评分（主入口）

    每只股票每交易日都产生评分，0-100分。
    不再依赖稀缺的模式信号。

    返回:
        {
            "score": 0-100,
            "signal": True if score >= 60 else False,
            "details": {各子项得分},
        }
    """
    if df.empty:
        return {"score": 0, "signal": False, "details": {"error": "no_data"}}

    ma = score_ma_position(df)
    vol = score_volume(df)
    mom = score_momentum(df)
    rsi_score_val = score_rsi_kdj(df)

    total = ma + vol + mom + rsi_score_val
    total = min(100, max(0, total))

    # 如果大盘是调整期（通过config判定），略微收紧
    # 这个由 signal.py 的权重控制，这里不做二次收紧

    return {
        "score": round(total, 1),
        "signal": total >= 60,
        "details": {
            "ma_score": round(ma, 1),
            "volume_score": round(vol, 1),
            "momentum_score": round(mom, 1),
            "rsi_score": round(rsi_score_val, 1),
            "n_bars": len(df),
        },
        "primary_signal": "continuous",
    }


# ─────────────────────────────────────────────────────────────────
# 向下兼容接口（原有的模式检测保留但不再使用）
# ─────────────────────────────────────────────────────────────────

def composite_entry_score(df: pd.DataFrame, config: dict = None, board: str = "创业板") -> dict:
    """综合入场评分（兼容旧接口，内部使用连续评分）

    Args:
        df: 含完整技术指标的日线DataFrame
        config: 根配置（不传则使用默认参数）
        board: 板块名称（不再区分，统一处理）

    Returns:
        {"score": 0-100, "signal": bool, "primary_signal": str, "signals": {...}}
    """
    result = continuous_entry_score(df, config)
    return {
        "score": result["score"],
        "signal": result["signal"],
        "primary_signal": result.get("primary_signal", "none"),
        "signals": {"continuous": result},
        "details": result.get("details", {}),
    }


# ─────────────────────────────────────────────────────────────────
# 保留旧接口签名（兼容 import），但内部都指向连续评分
# ─────────────────────────────────────────────────────────────────

def detect_breakout(df, config=None):
    return {"signal": False, "score": 0, "strength": "none", "details": {"deprecated": True}}

def detect_retrace_ma20(df, config=None, board="创业板"):
    return {"signal": False, "score": 0, "strength": "none", "details": {"deprecated": True}}

def detect_wave2(df, config=None):
    return {"signal": False, "score": 0, "strength": "none", "details": {"deprecated": True}}

def detect_vcp(df, config=None):
    return {"signal": False, "score": 0, "strength": "none", "details": {"deprecated": True}}
