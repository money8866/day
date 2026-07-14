#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V6.0 - 生命周期识别模块（分析师标准用词版）

阶段体系（6大阶段）：
  筑底 -> 启动 -> 主升 -> 高潮 -> 调整 -> 衰退
  每个阶段有子阶段细分

核心原则：Stage 必须与 Continuation 一致
  - continuation 极低 -> 趋势已破，只能处于 调整/筑底
  - 主升/高潮需要 continuation 确认
"""
import os, sys, warnings
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
warnings.filterwarnings("ignore")
import config


def identify_stage(trend, sentiment, capital, heat=50, limit_up=0,
                   drawdown=0, momentum=0, continuation=50, r5=0, r10=0):
    """识别生命周期阶段（分析师标准用词）

    continuation: 趋势延续评分（0-100），用于约束阶段判断
    r5/r10: 5日/10日收益率（百分点），用于判断阶段内趋势方向
    momentum: 混合动量（百分点），用于辅助判断
    """
    # ===== 前置约束：趋势延续极低 = 趋势已破 =====
    # continuation < 30 = 趋势已终结，不可能处于主升/扩张
    if continuation < 30:
        if trend < 50 and sentiment < 50:
            base = "衰退"
        else:
            base = "调整"  # 趋势已破但仍有残余动量 -> 调整
    # continuation < 40 = 趋势明显走弱
    elif continuation < 40 and sentiment < 50:
        base = "调整"
    # ===== 正常阶段判断（需 continuation 确认）=====
    elif trend >= 90 and sentiment >= 75 and heat > 80 and continuation >= 60:
        base = "高潮"
    elif trend >= 75 and capital >= 60 and sentiment >= 55 and continuation >= 55:
        base = "主升"
    elif (trend >= 55 and sentiment > 50 and capital > 40
            and drawdown < 10 and continuation >= 50):
        base = "主升"  # 原扩张：中等以上趋势+情绪+资金确认
    elif 35 <= trend < 65 and sentiment > 45 and momentum > -3:
        base = "启动"
    elif trend < 55 and sentiment < 50 and (drawdown > 15 or momentum < -5):
        base = "衰退"
    elif capital < 35 and trend < 50:
        base = "衰退"
    elif trend >= 60 and continuation >= 55:
        base = "主升"  # 趋势延续好 -> 主升
    elif continuation >= 50:
        base = "启动"
    elif trend >= 45:
        base = "筑底"
    else:
        base = "衰退"

    # ===== 子阶段精细化 =====
    if base == "主升":
        # 情绪冰点或短期动量持续下行 -> 主升回调
        if sentiment < 30 or (r5 < -2 and r10 < 0):
            return "主升回调"
        # 情绪高涨且短期动量强劲 -> 主升加速
        if sentiment > 55 and r5 > 2:
            return "主升加速"
        return "主升"

    if base == "启动":
        # 动量加速转正 -> 启动加速
        if r5 > 3 and momentum > 0:
            return "启动加速"
        # 仍在下跌 -> 启动初期
        if r5 < -1 and momentum < -2:
            return "启动初期"
        return "启动"

    if base == "调整":
        # 趋势尚未完全崩溃 -> 调整
        if trend >= 45:
            return "高位调整"
        return "调整"

    if base == "衰退":
        # 刚进入衰退，趋势尚未完全崩溃 -> 衰退初期
        if trend >= 45 and sentiment >= 35:
            return "衰退初期"
        return "衰退"

    if base == "高潮":
        # 情绪从高点回落 -> 高潮见顶
        if sentiment < 85 or r5 < 0:
            return "高潮见顶"
        return "高潮"

    if base == "筑底":
        if r5 > 1 and momentum > 0:
            return "筑底回升"
        return "筑底"

    return base


def _base_stage(stage):
    """从子阶段提取基础阶段名"""
    if "主升" in stage:
        return "主升"
    if "启动" in stage:
        return "启动"
    if "调整" in stage:
        return "调整"
    if "衰退" in stage:
        return "衰退"
    if "高潮" in stage:
        return "高潮"
    if "筑底" in stage:
        return "筑底"
    return stage


def stage_bonus(stage):
    base = _base_stage(stage)
    return config.LIFECYCLE_BONUS.get(base, 0)
