#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V7 - 综合评分调度器 (Score V7)

V7 与 V6 的核心差异：
  1. 资金分从"存量资金大小"改为"资金活跃与弹性"
  2. 新增"梯队完整度与扩散"维度 (30%)
  3. 趋势分引入 RSRS/Squeeze/MA 多头排列
  4. 新增硬性过滤规则降级系统
  5. 阶段标签基于梯队结构重新分类

评分权重架构：
  - 资金活跃与弹性 (35%)
  - 梯队完整度与扩散 (30%)
  - 趋势动量与连贯性 (20%)
  - 综合量价修正 (15%) — 保留V6的成交量比/涨跌幅修正

阶段标签体系：
  - 启动期: 结构初具雏形，梯队开始形成
  - 主升期: 梯队完整，资金活跃，趋势完美
  - 主升加速: 梯队完整+龙头创新高+情绪高涨
  - 分歧期: 梯队有缺陷，资金开始分歧
  - 退潮期: 梯队崩溃，资金萎缩
"""

import numpy as np
import pandas as pd
from datetime import datetime

from capital_v2 import compute_capital_vitality
from echelon import compute_echelon_score
from trend_v2 import compute_trend_momentum
from hard_rules import apply_hard_rules


def calculate_theme_score_v7(daily, codes, daily_basic=None, top_df=None, limit_df=None):
    """V7综合评分主函数

    参数:
        daily: DataFrame, 全市场日线 (含 ts_code, trade_date, pct_chg, amount, vol, close, high, low)
        codes: list, 主题成分股代码
        daily_basic: DataFrame, 每日基本面 (含 turnover_rate, circ_mv)
        top_df: DataFrame, 龙虎榜数据（可选）
        limit_df: DataFrame, 涨停数据（可选）

    返回:
        dict: {
            "composite_score": float,  # 综合评分 0-100
            "capital_vitality": float,  # 资金活跃与弹性
            "echelon_integrity": float,  # 梯队完整度
            "trend_momentum": float,  # 趋势动量
            "stage": str,  # 阶段标签
            "signal": str,  # 交易信号
            "penalties": list,  # 触发的惩罚项
            "sub_metrics": dict,  # 子维度明细
        }
    """
    # ============================================================
    # 1. 计算三大核心维度
    # ============================================================
    capital_score, cap_sub = compute_capital_vitality(daily, daily_basic, codes)
    echelon_score, ech_sub = compute_echelon_score(daily, codes, top_df, limit_df)
    trend_score, trend_sub = compute_trend_momentum(daily, codes)

    # ============================================================
    # 2. 综合量价修正 (15%)
    # ============================================================
    correction_score = _calc_volume_price_correction(daily, codes)

    # ============================================================
    # 3. 加权合成初始评分
    # ============================================================
    raw_score = (
        capital_score * 0.35 +
        echelon_score * 0.30 +
        trend_score * 0.20 +
        correction_score * 0.15
    )

    # ============================================================
    # 4. 硬性过滤规则
    # ============================================================
    adjusted_score, penalties = apply_hard_rules(
        raw_score, {}, daily, codes, daily_basic
    )

    # ============================================================
    # 5. 阶段标签与交易信号
    # ============================================================
    stage, signal = _classify_stage(capital_score, echelon_score, trend_score,
                                     adjusted_score, penalties)

    # ============================================================
    # 6. 汇总
    # ============================================================
    result = {
        "composite_score": round(adjusted_score, 1),
        "capital_vitality": round(capital_score, 1),
        "echelon_integrity": round(echelon_score, 1),
        "trend_momentum": round(trend_score, 1),
        "stage": stage,
        "signal": signal,
        "penalties": penalties,
        "sub_metrics": {
            **{f"cap_{k}": v for k, v in cap_sub.items()},
            **{f"ech_{k}": v for k, v in ech_sub.items()},
            **{f"trd_{k}": v for k, v in trend_sub.items()},
            "correction": round(correction_score, 1),
        },
    }

    return result


def _calc_volume_price_correction(daily, codes):
    """综合量价修正

    保留V6的成交量/涨跌幅修正逻辑，但简化为启发式规则。

    返回: 0-100
    """
    sub = daily[daily["ts_code"].isin(codes)].copy()
    if sub.empty:
        return 50.0

    latest_day = sub["trade_date"].max()
    latest = sub[sub["trade_date"] == latest_day]

    if latest.empty:
        return 50.0

    # ===== 1. 当日涨跌幅修正 =====
    pct_mean = latest["pct_chg"].mean()
    pct_median = latest["pct_chg"].median()

    # 当日涨跌幅 > 0 = 加分，< 0 = 减分
    if pct_median > 0:
        vol_score = 50 + min(pct_median * 2, 30)
    elif pct_median > -0.5:
        vol_score = 50 + max(pct_median * 2, -10)
    else:
        vol_score = 40 + max(pct_median * 2, -20)

    # ===== 2. 成交量修正 =====
    amt = sub.groupby("trade_date")["amount"].sum().sort_index()
    if len(amt) >= 5:
        current_amt = amt.iloc[-1]
        ma5_amt = amt.iloc[-5:].mean()
        if ma5_amt > 0:
            amt_ratio = current_amt / ma5_amt
            if amt_ratio > 1.3:
                vol_score += 10  # 放量
            elif amt_ratio > 1.1:
                vol_score += 5
            elif amt_ratio < 0.6:
                vol_score -= 10  # 缩量
            elif amt_ratio < 0.8:
                vol_score -= 5

    # ===== 3. 上涨家数修正 =====
    up_count = (latest["pct_chg"] > 0).sum()
    total_count = len(latest)
    up_ratio = up_count / total_count if total_count > 0 else 0.5

    if up_ratio > 0.7:
        vol_score += 5
    elif up_ratio < 0.3:
        vol_score -= 5

    # ===== 4. 涨停/跌停修正 =====
    limit_up = (latest["pct_chg"] > 9.5).sum()
    limit_down = (latest["pct_chg"] < -9.5).sum()

    if limit_up >= 2:
        vol_score += 10
    elif limit_up >= 1:
        vol_score += 5

    if limit_down >= 2:
        vol_score -= 10
    elif limit_down >= 1:
        vol_score -= 5

    return float(np.clip(vol_score, 5, 95))


def _classify_stage(capital_score, echelon_score, trend_score, adjusted_score, penalties):
    """阶段标签与交易信号分类

    V7阶段标签体系（基于梯队结构重新定义）：

    启动期 (Initiation):
        - 梯队开始形成，但尚未完整
        - 资金有初步活跃迹象
        - 趋势尚在酝酿
        -> 信号: 关注

    主升期 (Bullish):
        - 梯队完整度高
        - 资金活跃
        - 趋势完美
        -> 信号: 看多

    主升加速 (Accelerating):
        - 梯队完整 + 龙头创新高
        - 资金极度活跃
        - Squeeze突破
        -> 信号: 强买

    分歧期 (Divergence):
        - 梯队有缺陷（哑铃化或中军破位）
        - 资金开始分歧
        - 趋势仍在但减弱
        -> 信号: 谨慎

    退潮期 (Ebb):
        - 梯队崩溃
        - 资金萎缩
        - 趋势破坏
        -> 信号: 回避
    """
    has_penalty = len(penalties) > 0
    total_penalty = sum(abs(p.get("penalty", 0)) for p in penalties) if has_penalty else 0

    # 检查特定惩罚项
    penalty_names = [p.get("rule", "") for p in penalties] if has_penalty else []
    has_backbone_breakdown = "backbone_breakdown" in penalty_names
    has_dumbbell = "dumbbell_echelon" in penalty_names
    has_volume_shrink = "volume_shrink" in penalty_names
    has_overheat = "sentiment_overheat" in penalty_names

    # ===== 阶段判定 =====
    # 退潮期
    if has_backbone_breakdown or (has_volume_shrink and adjusted_score < 30):
        stage = "退潮期"
        signal = "回避"
    # 主升加速
    elif adjusted_score >= 70 and echelon_score >= 65 and capital_score >= 65 and trend_score >= 60 and not has_penalty:
        stage = "主升加速"
        signal = "强买"
    # 主升期
    elif adjusted_score >= 60 and echelon_score >= 55 and capital_score >= 55:
        stage = "主升期"
        signal = "看多"
    # 分歧期
    elif has_dumbbell or has_overheat or (has_penalty and adjusted_score >= 40):
        stage = "分歧期"
        signal = "谨慎"
    # 启动期
    elif adjusted_score >= 40 and capital_score >= 45:
        stage = "启动期"
        signal = "关注"
    # 退潮期（低分）
    elif adjusted_score < 30:
        stage = "退潮期"
        signal = "回避"
    # 默认
    else:
        stage = "混沌期"
        signal = "中性"

    return stage, signal