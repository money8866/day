#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V3.0 - 生命周期识别模块
"""
import os
import sys
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
sys.path.insert(0, PROJECT_ROOT)

def identify_lifecycle_stage(trend_score, sentiment_score, capital_score, heat=50, limit_up_count=0, drawdown=0, momentum=0):
    """识别生命周期阶段"""
    
    # ==================== 判断规则 ====================
    
    # Birth: 趋势较低但开始上升，情绪开始升温，资金开始流入
    if trend_score < 70 and sentiment_score > 50 and capital_score > 40 and drawdown < 10:
        return "Birth"
    
    # Expansion: 趋势良好，情绪升温，资金持续流入，尚未到顶
    if 60 < trend_score < 90 and sentiment_score > 55 and capital_score > 50:
        return "Expansion"
    
    # MainTrend: 趋势强劲，情绪稳定，资金持续
    if trend_score >= 80 and sentiment_score >= 60 and capital_score >= 60:
        return "MainTrend"
    
    # Climax: 趋势极高但可能减速，情绪爆棚，资金过热
    if trend_score >= 90 and sentiment_score >= 80 and (heat > 80 or limit_up_count > 10):
        return "Climax"
    
    # Decline: 趋势下降，情绪回落，资金流出
    if trend_score < 60 and sentiment_score < 50 and (drawdown > 15 or momentum < -5):
        return "Decline"
    
    # 默认：Expansion
    return "Expansion"

def calculate_lifecycle_score(stage):
    """计算生命周期加分"""
    from config import LIFECYCLE_BONUS
    return LIFECYCLE_BONUS.get(stage, 0)

if __name__ == "__main__":
    print("[Lifecycle] 生命周期模块加载完成")
    print("  Birth: +20, Expansion: +15, MainTrend: +10, Climax: -10, Decline: -30")
