#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V3.0 - 综合评分和信号生成模块
"""
import os
import sys
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
sys.path.insert(0, PROJECT_ROOT)

import config

def calculate_composite_score(trend_score, capital_score, sentiment_score,
                               persistence_score, lifecycle_bonus, leader_score, risk_score):
    """计算综合评分"""
    
    composite = (
        trend_score * config.WEIGHTS["trend"] +
        capital_score * config.WEIGHTS["capital"] +
        sentiment_score * config.WEIGHTS["sentiment"] +
        persistence_score * config.WEIGHTS["persistence"] +
        (50 + lifecycle_bonus) * config.WEIGHTS["lifecycle"] +
        leader_score * config.WEIGHTS["leader"] +
        (100 - risk_score) * config.WEIGHTS["risk"]
    )
    
    return max(0, min(100, composite))

def generate_trade_signal(composite_score, capital_score, trend_score, stage):
    """生成交易信号"""
    
    thresholds = config.SIGNAL_THRESHOLDS["strong_buy"]
    
    if (composite_score >= thresholds["composite"] and
        capital_score >= thresholds["capital"] and
        trend_score >= thresholds["trend"] and
        stage in thresholds["stages"]):
        return "Strong Buy"
    
    if composite_score >= config.SIGNAL_THRESHOLDS["watch"]:
        return "Watch"
    
    if composite_score >= config.SIGNAL_THRESHOLDS["hold"]:
        return "Hold"
    
    return "Avoid"

def calculate_confidence(composite_score, trend_score, capital_score):
    """计算置信度"""
    confidence = (
        composite_score * 0.5 +
        trend_score * 0.25 +
        capital_score * 0.25
    )
    return max(0, min(100, confidence))

if __name__ == "__main__":
    print("[Composite] 综合评分模块加载完成")
