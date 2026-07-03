#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V6.0 - 生命周期识别模块
"""
import os, sys, warnings
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
warnings.filterwarnings("ignore")
import config


def identify_stage(trend, sentiment, capital, heat=50, limit_up=0,
                   drawdown=0, momentum=0):
    """识别生命周期阶段"""
    if trend >= 90 and sentiment >= 75 and heat > 80:
        return "高潮"
    if trend >= 75 and capital >= 60 and sentiment >= 55:
        return "主升"
    if trend >= 55 and sentiment > 50 and capital > 40 and drawdown < 10:
        return "扩张"
    if 35 <= trend < 65 and sentiment > 45 and momentum > -3:
        return "启动"
    if trend < 55 and sentiment < 50 and (drawdown > 15 or momentum < -5):
        return "衰退"
    if capital < 35 and trend < 50:
        return "衰退"
    if trend >= 60:
        return "扩张"
    return "启动"


def stage_bonus(stage):
    return config.LIFECYCLE_BONUS.get(stage, 0)
