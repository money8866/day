#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Theme Alpha Engine V6.0 - 综合评分与信号"""
import os, sys, warnings
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
warnings.filterwarnings("ignore")
import config


def compute_composite(trend, capital, sentiment, persistence,
                      lifecycle_bonus, leader_score, risk):
    # Lifecycle: 基础分 50*权重 + bonus 加权（权重放大到 0.15，拉开启动/衰退差距）
    lifecycle_contrib = 50 * config.W_LIFECYCLE + lifecycle_bonus * 0.15
    c = (trend * config.W_TREND +
         capital * config.W_CAPITAL +
         sentiment * config.W_SENTIMENT +
         persistence * config.W_PERSISTENCE +
         lifecycle_contrib +
         leader_score * config.W_LEADER +
         (100 - risk) * config.W_RISK_INV)
    return float(np.clip(c, 0, 100))


def trade_signal(composite, capital, trend, stage):
    if (composite >= config.SB_COMPOSITE and
        capital >= config.SB_CAPITAL and
        trend >= config.SB_TREND and
        stage in config.SB_STAGES):
        return "强买"
    if composite >= config.WATCH_COMPOSITE:
        return "关注"
    if composite >= config.HOLD_COMPOSITE:
        return "持有"
    return "回避"


def confidence(composite, trend, capital):
    return float(np.clip(composite * 0.5 + trend * 0.25 + capital * 0.25, 0, 100))
