#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Theme Alpha Engine V6.2 - 综合评分与信号

V6.2 核心转变：
  从 Current Heat -> Future Alpha
  Forward Alpha Score 权重 35%，成为最大单一维度
  旧维度（趋势/资金/情绪等）降权为"确认因子"而非"驱动因子"
"""
import os, sys, warnings
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
warnings.filterwarnings("ignore")
import config


def compute_composite(trend, capital, sentiment, persistence,
                      lifecycle_bonus, leader_score, risk, continuation,
                      today_return=None, forward_alpha=None):
    """综合评分公式 (0-100)

    V6.2 架构：
      预测层 (35%): Forward Alpha - 动量加速 + 反转张力 + 聪明钱背离
      确认层 (65%): 趋势/资金/情绪/延续/持续性/生命周期/龙头/风险
                    + 当日跌幅惩罚

    forward_alpha: Forward Alpha 预测分 (0-100)，来自 forward_alpha.py
    """
    lifecycle_contrib = 50 * config.W_LIFECYCLE + lifecycle_bonus * 0.15

    # ===== 确认层 =====
    confirm_layer = (trend * config.W_TREND +
                     capital * config.W_CAPITAL +
                     sentiment * config.W_SENTIMENT +
                     persistence * config.W_PERSISTENCE +
                     continuation * config.W_CONTINUATION +
                     lifecycle_contrib +
                     leader_score * config.W_LEADER +
                     (100 - risk) * config.W_RISK_INV)

    # ===== 预测层 =====
    if forward_alpha is not None:
        c = forward_alpha * config.W_FORWARD_ALPHA + confirm_layer
    else:
        # 无Forward Alpha时，确认层权重自动归一化
        total_confirm = (config.W_TREND + config.W_CAPITAL + config.W_SENTIMENT +
                         config.W_PERSISTENCE + config.W_CONTINUATION +
                         config.W_LIFECYCLE + config.W_LEADER + config.W_RISK_INV)
        c = confirm_layer / total_confirm * (1 - config.W_FORWARD_ALPHA) + confirm_layer * 0.65

    # ===== 当日跌幅惩罚（防失真） =====
    if today_return is not None:
        if today_return <= -7:
            c -= 15
        elif today_return <= -5:
            c -= 10
        elif today_return <= -3:
            c -= 6

    return float(np.clip(c, 0, 100))


def trade_signal(composite, capital, trend, stage, continuation,
                 forward_alpha=None, forward_signal=None):
    """交易信号：V6.2 双层触发机制

    层1：Forward Alpha 预测层（未来5日超额收益概率）
    层2：Current Heat 确认层（当前是否已启动）

    - 强买：Future Alpha强 + 当前确认强（启动/主升阶段）
    - 看多：Future Alpha强 或 分歧买点（当前弱但未来强）
    - 中性：Future Alpha中等
    - 看空：Future Alpha弱
    - 强烈看空：Future Alpha极弱 + 趋势破坏
    """
    # ===== 层1：Forward Alpha 预测触发 =====
    fa_strong = forward_alpha is not None and forward_alpha >= 65
    fa_moderate = forward_alpha is not None and forward_alpha >= 50
    fa_weak = forward_alpha is not None and forward_alpha < 38
    fa_very_weak = forward_alpha is not None and forward_alpha < 25

    # ===== 层2：Current Heat 确认 =====
    heat_strong = (composite >= config.SB_COMPOSITE and
                   continuation >= config.SB_CONTINUATION and
                   capital >= config.SB_CAPITAL and
                   trend >= config.SB_TREND and
                   stage in config.SB_STAGES)

    # 强买：Future Alpha强 + Current Heat确认
    if fa_strong and heat_strong:
        return "强买"

    # 看多：Future Alpha强 + 启动/主升阶段（即使当前热度不够）
    if fa_strong and stage in config.SB_STAGES:
        return "看多"

    # 看多：Forward Alpha强 + 综合分中等
    if fa_strong and composite >= 55:
        return "看多"

    # 分歧买点：当前综合分低但Future Alpha很高
    if (forward_alpha is not None and forward_alpha >= 62
        and composite < config.WATCH_DIV_COMPOSITE
        and stage in config.SB_STAGES):
        return "看多"

    # 中性：Forward Alpha中等
    if fa_moderate:
        if composite >= config.WATCH_COMPOSITE:
            return "关注"
        return "中性"

    # 无Forward Alpha时降级到原逻辑
    if forward_alpha is None:
        if heat_strong:
            return "强买"
        if composite >= config.WATCH_COMPOSITE:
            return "关注"
        if (continuation >= config.WATCH_CONTINUATION
            and composite < config.WATCH_DIV_COMPOSITE
            and stage in config.SB_STAGES):
            return "关注"
        if stage in ("主升", "主升加速", "主升回调") and continuation >= 70 and composite >= config.HOLD_COMPOSITE:
            return "持有"
        return "回避"

    # 看空：Forward Alpha弱
    if fa_weak:
        if fa_very_weak:
            return "强烈看空"
        return "看空"

    # 兜底
    if composite >= config.WATCH_COMPOSITE and stage in config.SB_STAGES:
        return "关注"
    return "中性"


def confidence(composite, trend, capital, continuation, forward_alpha=None):
    """置信度：Future Alpha 权重最大

    V6.2: Forward Alpha 占 40% 置信度权重
    """
    if forward_alpha is not None:
        return float(np.clip(
            forward_alpha * 0.40 + composite * 0.20 + trend * 0.10 +
            capital * 0.10 + continuation * 0.20,
            0, 100
        ))
    return float(np.clip(
        composite * 0.35 + trend * 0.20 + capital * 0.20 + continuation * 0.25,
        0, 100
    ))
