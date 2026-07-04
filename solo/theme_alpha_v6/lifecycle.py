#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V6.0 - 生命周期识别模块

核心原则：Stage 必须与 Continuation 一致
  - continuation 极低 → 不可能是扩张/主升/高潮
  - 扩张/主升需要 continuation 确认
"""
import os, sys, warnings
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
warnings.filterwarnings("ignore")
import config


def identify_stage(trend, sentiment, capital, heat=50, limit_up=0,
                   drawdown=0, momentum=0, continuation=50):
    """识别生命周期阶段

    continuation: 趋势延续评分（0-100），用于约束阶段判断
    """
    # ===== 前置约束：趋势延续极低时不可能处于强势阶段 =====
    # continuation < 30 = 趋势已破坏
    if continuation < 30:
        if trend < 50 and sentiment < 50:
            return "衰退"
        return "启动"  # 趋势破坏但动量还在 → 降级为启动而非扩张
    # continuation < 40 = 趋势明显走弱
    if continuation < 40 and sentiment < 50:
        return "启动"

    # ===== 正常阶段判断（需 continuation 确认）=====
    if trend >= 90 and sentiment >= 75 and heat > 80 and continuation >= 60:
        return "高潮"
    if trend >= 75 and capital >= 60 and sentiment >= 55 and continuation >= 55:
        return "主升"
    if (trend >= 55 and sentiment > 50 and capital > 40
            and drawdown < 10 and continuation >= 50):
        return "扩张"
    if 35 <= trend < 65 and sentiment > 45 and momentum > -3:
        return "启动"
    if trend < 55 and sentiment < 50 and (drawdown > 15 or momentum < -5):
        return "衰退"
    if capital < 35 and trend < 50:
        return "衰退"
    # 兜底：trend >= 60 需要 continuation 确认才能判为扩张
    if trend >= 60 and continuation >= 55:
        return "扩张"
    # 趋势延续好但动量低 → 即将启动
    if continuation >= 50:
        return "启动"
    if trend >= 45:
        return "启动"
    return "衰退"


def stage_bonus(stage):
    return config.LIFECYCLE_BONUS.get(stage, 0)
