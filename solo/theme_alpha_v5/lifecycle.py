#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V5.0 - 生命周期识别模块

自动识别：Birth → Expansion → MainTrend → Climax → Decline
"""
import os, sys, warnings
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(BASE_DIR))
sys.path.insert(0, BASE_DIR)
warnings.filterwarnings("ignore")

import config


def identify_stage(trend: float, sentiment: float, capital: float,
                   heat: float = 50, limit_up: int = 0,
                   drawdown: float = 0, momentum: float = 0) -> str:
    """识别生命周期阶段"""
    if trend >= 90 and sentiment >= 75 and heat > 80:
        return "Climax"
    if trend >= 75 and capital >= 60 and sentiment >= 55:
        return "MainTrend"
    if trend >= 55 and sentiment > 50 and capital > 40 and drawdown < 10:
        return "Expansion"
    if 35 <= trend < 65 and sentiment > 45 and momentum > -3:
        return "Birth"
    if trend < 55 and sentiment < 50 and (drawdown > 15 or momentum < -5):
        return "Decline"
    if capital < 35 and trend < 50:
        return "Decline"

    # 默认归类
    if trend >= 60:
        return "Expansion"
    return "Birth"


def stage_bonus(stage: str) -> int:
    """生命周期加分"""
    return config.LIFECYCLE_BONUS.get(stage, 0)


if __name__ == "__main__":
    print("[Lifecycle] 生命周期模块加载完成")
