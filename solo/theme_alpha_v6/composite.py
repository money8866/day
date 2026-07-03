#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Theme Alpha Engine V6.0 - 综合评分与信号"""
import os, sys, warnings
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
warnings.filterwarnings("ignore")
import config


def compute_composite(trend, capital, sentiment, persistence,
                      lifecycle_bonus, leader_score, risk, continuation):
    """综合评分公式 (0-100)
    新增 Continuation 维度，识别强势延续 + 分歧买点
    """
    lifecycle_contrib = 50 * config.W_LIFECYCLE + lifecycle_bonus * 0.15
    c = (trend * config.W_TREND +
         capital * config.W_CAPITAL +
         sentiment * config.W_SENTIMENT +
         persistence * config.W_PERSISTENCE +
         continuation * config.W_CONTINUATION +
         lifecycle_contrib +
         leader_score * config.W_LEADER +
         (100 - risk) * config.W_RISK_INV)
    return float(np.clip(c, 0, 100))


def trade_signal(composite, capital, trend, stage, continuation):
    """交易信号：Composite 与 ContinuationScore 双触发机制
    - 强买：综合强 + 延续强 + 启动/扩张阶段
    - 关注：综合强 OR 真分歧买点（综合低 + 延续很高）
    - 持有：综合中等 OR 延续中等
    - 回避：两者都弱
    """
    # 强买：已经是强势 + 延续概率高 + 阶段匹配
    if (composite >= config.SB_COMPOSITE and
        continuation >= config.SB_CONTINUATION and
        capital >= config.SB_CAPITAL and
        trend >= config.SB_TREND and
        stage in config.SB_STAGES):
        return "强买"

    # 关注：综合强
    if composite >= config.WATCH_COMPOSITE:
        return "关注"
    # 分歧买点：综合分确实低（真分歧）+ 延续分很高（趋势未破）+ 阶段匹配
    if (continuation >= config.WATCH_CONTINUATION
        and composite < config.WATCH_DIV_COMPOSITE
        and stage in config.SB_STAGES):
        return "关注"

    # 持有：综合中等 OR 延续中等
    if composite >= config.HOLD_COMPOSITE:
        return "持有"
    if continuation >= config.HOLD_CONTINUATION:
        return "持有"

    return "回避"


def confidence(composite, trend, capital, continuation):
    """置信度：综合分 + 趋势 + 资金 + 延续"""
    return float(np.clip(
        composite * 0.35 + trend * 0.20 + capital * 0.20 + continuation * 0.25,
        0, 100
    ))
