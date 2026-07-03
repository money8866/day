#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V5.0 - 综合评分与信号生成模块
"""
import os, sys, warnings
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(BASE_DIR))
sys.path.insert(0, BASE_DIR)
warnings.filterwarnings("ignore")

import config


def compute_composite(trend: float, capital: float, sentiment: float,
                      persistence: float, lifecycle_bonus: int,
                      leader_score: float, risk: float) -> float:
    """综合评分 = Σ(维度分 × 权重)"""
    c = (trend * config.W_TREND +
         capital * config.W_CAPITAL +
         sentiment * config.W_SENTIMENT +
         persistence * config.W_PERSISTENCE +
         (50 + lifecycle_bonus) * config.W_LIFECYCLE +
         leader_score * config.W_LEADER +
         (100 - risk) * config.W_RISK_INV)
    return float(np.clip(c, 0, 100))


def trade_signal(composite: float, capital: float, trend: float, stage: str) -> str:
    """生成交易信号"""
    if (composite >= config.SB_COMPOSITE and
        capital >= config.SB_CAPITAL and
        trend >= config.SB_TREND and
        stage in ("Birth", "Expansion")):
        return "Strong Buy"
    if composite >= config.WATCH_COMPOSITE:
        return "Watch"
    if composite >= config.HOLD_COMPOSITE:
        return "Hold"
    return "Avoid"


def confidence(composite: float, trend: float, capital: float) -> float:
    """置信度"""
    return float(np.clip(composite * 0.5 + trend * 0.25 + capital * 0.25, 0, 100))


if __name__ == "__main__":
    print("[Composite] 综合评分模块加载完成")
