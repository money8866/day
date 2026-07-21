#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V7 - 硬性过滤规则 (Hard Rules / Post-processing)

核心目标：识别"虚假繁荣"主题，进行降级或一票否决。

设计哲学：
  高评分不一定等于高可交易性。
  以下规则用于识别"伪强势"主题，防止踩坑。

规则列表：
  1. 假大阳拉指数 - 权重股突然拉一根大阳，但跟风股全跌
  2. 中军破位 - 主题底盘（权重股）跌破关键支撑
  3. 成交量极度萎缩 - 量能萎缩到20日均量的50%以下
  4. 梯队哑铃化 - 龙头涨+跟风跌 = 游资拉高出货
  5. 情绪透支 - 近5日涨幅过大且获利盘过重
  6. 虚假扩散 - 涨幅>3%富豪数少但权重股贡献大
"""

import numpy as np
import pandas as pd


def apply_hard_rules(score, sub_metrics, daily, codes, daily_basic=None):
    """硬性过滤规则主函数

    参数:
        score: float, 原始综合评分
        sub_metrics: dict, 各子维度评分明细
        daily: DataFrame, 全市场日线
        codes: list, 主题成分股代码
        daily_basic: DataFrame, 每日基本面（可选）

    返回:
        adjusted_score: float, 调整后评分
        penalties: list of dict, 触发的惩罚项
    """
    sub = daily[daily["ts_code"].isin(codes)].copy()
    if sub.empty:
        return score, []

    latest_day = sub["trade_date"].max()
    latest = sub[sub["trade_date"] == latest_day].copy()

    if latest.empty:
        return score, []

    penalties = []

    # ============================================================
    # 规则1: 假大阳拉指数 (False Big Candle)
    # ============================================================
    score, p1 = _rule_false_big_candle(score, sub, latest, codes)
    penalties.extend(p1)

    # ============================================================
    # 规则2: 中军破位 (Backbone Breakdown)
    # ============================================================
    score, p2 = _rule_backbone_breakdown(score, sub, latest, codes)
    penalties.extend(p2)

    # ============================================================
    # 规则3: 成交量极度萎缩 (Volume Shrink)
    # ============================================================
    score, p3 = _rule_volume_shrink(score, sub, codes)
    penalties.extend(p3)

    # ============================================================
    # 规则4: 梯队哑铃化 (Dumbbell Echelon)
    # ============================================================
    score, p4 = _rule_dumbbell_echelon(score, latest, codes)
    penalties.extend(p4)

    # ============================================================
    # 规则5: 情绪透支 (Sentiment Overheat)
    # ============================================================
    score, p5 = _rule_sentiment_overheat(score, sub, codes)
    penalties.extend(p5)

    # ============================================================
    # 规则6: 虚假扩散 (False Diffusion)
    # ============================================================
    score, p6 = _rule_false_diffusion(score, sub, latest, codes)
    penalties.extend(p6)

    # 最终评分不得低于0
    adjusted_score = float(np.clip(score, 0, 100))

    return adjusted_score, penalties


def _rule_false_big_candle(score, sub, latest, codes):
    """规则1: 假大阳拉指数

    场景：Top 1-2 权重股拉出一根大阳线（>5%），
          但其余成分股全部下跌或涨幅<1%。
    特征：涨幅集中度极高，市场情绪不跟。

    惩罚：-10 ~ -25分
    """
    if latest.empty or len(codes) < 5:
        return score, []

    pct = latest["pct_chg"].dropna()
    if len(pct) < 5:
        return score, []

    # 按涨幅排序
    sorted_pct = pct.sort_values(ascending=False)

    top1_ret = sorted_pct.iloc[0] if len(sorted_pct) >= 1 else 0
    top2_ret = sorted_pct.iloc[1] if len(sorted_pct) >= 2 else 0
    median_ret = sorted_pct.median()
    bottom_ret = sorted_pct.iloc[-1] if len(sorted_pct) >= 1 else 0

    # 条件：Top1 > 5% 且 中位数 < 0.5% 或 底部大跌
    if top1_ret > 5 and (median_ret < 0.5 or bottom_ret < -3):
        # 计算集中度
        top1_share = top1_ret / pct.sum() if pct.sum() > 0 else 0
        if top1_share > 0.5:  # 第一只股票贡献了超过50%的涨幅
            penalty = -20
            reason = f"假大阳: Top1涨{top1_ret:.1f}%, 但中位数仅{median_ret:.1f}%, 涨幅集中度{top1_share:.0%}"
            return score + penalty, [{"rule": "false_big_candle", "penalty": penalty, "reason": reason}]
        elif top1_share > 0.3:
            penalty = -12
            reason = f"假大阳: Top1涨{top1_ret:.1f}%, 涨幅集中度{top1_share:.0%}"
            return score + penalty, [{"rule": "false_big_candle", "penalty": penalty, "reason": reason}]

    return score, []


def _rule_backbone_breakdown(score, sub, latest, codes):
    """规则2: 中军破位

    场景：主题的权重股（市值前20%）中有超过50%的股票
          跌破20日均线，且跌幅大于-3%。
    特征：底盘不稳，主题随时崩塌。

    惩罚：-15 ~ -30分
    """
    if latest.empty or len(codes) < 5:
        return score, []

    # 按成交额排序取前20%作为中军
    latest_amt = latest.groupby("ts_code")["amount"].sum().sort_values(ascending=False)
    if latest_amt.empty:
        return score, []

    n_backbone = max(1, len(latest_amt) // 5)
    backbone_codes = latest_amt.head(n_backbone).index.tolist()

    if len(backbone_codes) < 2:
        return score, []

    breakdown_count = 0
    heavy_breakdown_count = 0  # 严重破位（跌幅>3%）

    for code in backbone_codes:
        stock = sub[sub["ts_code"] == code].sort_values("trade_date")
        if len(stock) < 20:
            continue

        last_close = stock["close"].iloc[-1]
        ma20 = stock["close"].iloc[-20:].mean()

        # 跌破20日线
        if last_close < ma20 * 0.98:
            breakdown_count += 1
            if last_close < ma20 * 0.95:  # 严重破位
                heavy_breakdown_count += 1

    breakdown_ratio = breakdown_count / len(backbone_codes)
    heavy_ratio = heavy_breakdown_count / len(backbone_codes)

    if heavy_ratio > 0.5:
        penalty = -30
        reason = f"中军严重破位: {heavy_ratio:.0%}中军跌破5%"
        return score + penalty, [{"rule": "backbone_breakdown", "penalty": penalty, "reason": reason}]
    elif breakdown_ratio > 0.5:
        penalty = -20
        reason = f"中军破位: {breakdown_ratio:.0%}中军跌破2%"
        return score + penalty, [{"rule": "backbone_breakdown", "penalty": penalty, "reason": reason}]
    elif breakdown_ratio > 0.3:
        penalty = -10
        reason = f"中军偏弱: {breakdown_ratio:.0%}中军破位"
        return score + penalty, [{"rule": "backbone_breakdown", "penalty": penalty, "reason": reason}]

    return score, []


def _rule_volume_shrink(score, sub, codes):
    """规则3: 成交量极度萎缩

    场景：当日成交额 < 20日均量的50%。
    特征：成交量萎缩意味着缺乏增量资金，即使价格上涨也是"无量反弹"。

    惩罚：-10 ~ -20分
    """
    if sub.empty:
        return score, []

    amt = sub.groupby("trade_date")["amount"].sum().sort_index()
    if len(amt) < 20:
        return score, []

    current_amt = amt.iloc[-1]
    ma20_amt = amt.iloc[-20:].mean()

    if ma20_amt <= 0:
        return score, []

    ratio = current_amt / ma20_amt

    if ratio < 0.3:
        penalty = -20
        reason = f"成交量极度萎缩: 当日为20日均量的{ratio:.0%}"
        return score + penalty, [{"rule": "volume_shrink", "penalty": penalty, "reason": reason}]
    elif ratio < 0.5:
        penalty = -12
        reason = f"成交量萎缩: 当日为20日均量的{ratio:.0%}"
        return score + penalty, [{"rule": "volume_shrink", "penalty": penalty, "reason": reason}]
    elif ratio < 0.7:
        penalty = -5
        reason = f"成交量偏弱: 当日为20日均量的{ratio:.0%}"
        return score + penalty, [{"rule": "volume_shrink", "penalty": penalty, "reason": reason}]

    return score, []


def _rule_dumbbell_echelon(score, latest, codes):
    """规则4: 梯队哑铃化

    场景：龙头（Top 2）涨幅很大，但跟风股（后50%）跌幅很大。
          典型的"拉高出货"结构：游资猛拉龙头，同时出售跟风股。
    特征：龙头涨 + 跟风跌 = 出货结构

    惩罚：-15 ~ -25分
    """
    if latest.empty or len(codes) < 8:
        return score, []

    pct = latest["pct_chg"].dropna()
    if len(pct) < 8:
        return score, []

    sorted_pct = pct.sort_values(ascending=False)

    top2_mean = sorted_pct.iloc[:2].mean()
    top3_5_mean = sorted_pct.iloc[2:5].mean() if len(sorted_pct) >= 5 else top2_mean
    bottom_half_mean = sorted_pct.iloc[len(sorted_pct) // 2:].mean()

    # 条件：龙头涨 > 3% 且 后50%跌 < -2%
    if top2_mean > 3 and bottom_half_mean < -2:
        # 龙头涨势和后50%的差距
        gap = top2_mean - bottom_half_mean
        if gap > 10:
            penalty = -25
            reason = f"梯队哑铃化: 龙头涨{top2_mean:.1f}%, 后50%跌{bottom_half_mean:.1f}%, 差距{gap:.1f}%"
            return score + penalty, [{"rule": "dumbbell_echelon", "penalty": penalty, "reason": reason}]
        elif gap > 6:
            penalty = -18
            reason = f"梯队哑铃化: 差距{gap:.1f}%"
            return score + penalty, [{"rule": "dumbbell_echelon", "penalty": penalty, "reason": reason}]

    return score, []


def _rule_sentiment_overheat(score, sub, codes):
    """规则5: 情绪透支

    场景：近5日涨幅累计过大（>15%），且成交额连续放大后开始萎缩。
    特征：获利盘过重，短期回调风险大。

    惩罚：-5 ~ -15分
    """
    if sub.empty:
        return score, []

    # 主题等权指数
    close_pivot = sub.pivot_table(
        index="trade_date", columns="ts_code", values="close", aggfunc="first"
    )
    if close_pivot.empty:
        return score, []

    norm = close_pivot / close_pivot.iloc[0] * 100
    index_close = norm.mean(axis=1)

    if len(index_close) < 10:
        return score, []

    # 近5日涨幅
    ret_5d = (index_close.iloc[-1] / index_close.iloc[-6] - 1) if len(index_close) >= 6 else 0
    # 近20日涨幅
    ret_20d = (index_close.iloc[-1] / index_close.iloc[-21] - 1) if len(index_close) >= 21 else 0

    # 成交额趋势
    amt = sub.groupby("trade_date")["amount"].sum().sort_index()
    if len(amt) >= 10:
        amt_5d = amt.iloc[-5:].mean()
        amt_10d = amt.iloc[-10:].mean()
        amt_ratio = amt_5d / amt_10d if amt_10d > 0 else 1
    else:
        amt_ratio = 1

    # 条件：近5日涨幅过大 + 量能开始萎缩
    if ret_5d > 0.15 and amt_ratio < 0.9:
        penalty = -15
        reason = f"情绪透支: 5日涨{ret_5d*100:.0f}%, 量能萎缩到{amt_ratio:.0%}"
        return score + penalty, [{"rule": "sentiment_overheat", "penalty": penalty, "reason": reason}]
    elif ret_5d > 0.10 and amt_ratio < 0.8:
        penalty = -10
        reason = f"情绪偏热: 5日涨{ret_5d*100:.0f}%, 量能萎缩到{amt_ratio:.0%}"
        return score + penalty, [{"rule": "sentiment_overheat", "penalty": penalty, "reason": reason}]
    elif ret_20d > 0.30 and amt_ratio < 0.7:
        penalty = -8
        reason = f"累计涨幅过大: 20日涨{ret_20d*100:.0f}%, 量能萎缩"
        return score + penalty, [{"rule": "sentiment_overheat", "penalty": penalty, "reason": reason}]

    return score, []


def _rule_false_diffusion(score, sub, latest, codes):
    """规则6: 虚假扩散

    场景：表面上很多股票在涨，但仔细看涨幅>3%的股票数量少，
          大市值权重股拉高了主题指数。
    特征：扩散是"假象"，实际赚钱效应有限。

    惩罚：-5 ~ -10分
    """
    if latest.empty or len(codes) < 8:
        return score, []

    pct = latest["pct_chg"].dropna()
    if len(pct) < 8:
        return score, []

    # 检查涨幅>3%的股票数量
    pct_gt_3 = (pct > 3).sum()
    pct_gt_0 = (pct > 0).sum()

    # 条件：上涨家数多但大涨家数少
    if pct_gt_0 > len(pct) * 0.5 and pct_gt_3 < 3:
        # 权重股是否贡献了大部分涨幅？
        # 用成交额加权涨幅
        latest_with_amt = latest[["ts_code", "pct_chg", "amount"]].dropna()
        if len(latest_with_amt) >= 8:
            # 计算成交额加权涨幅和等权涨幅的差异
            total_amt = latest_with_amt["amount"].sum()
            if total_amt > 0:
                weighted_ret = (latest_with_amt["pct_chg"] * latest_with_amt["amount"]).sum() / total_amt
                equal_ret = latest_with_amt["pct_chg"].mean()

                if weighted_ret > equal_ret * 1.5:  # 加权涨幅显著高于等权涨幅
                    penalty = -8
                    reason = f"虚假扩散: 涨{int(pct_gt_0)}家但仅{int(pct_gt_3)}家涨>3%, 加权涨幅{weighted_ret:.1f}%>等权{equal_ret:.1f}%"
                    return score + penalty, [{"rule": "false_diffusion", "penalty": penalty, "reason": reason}]

    return score, []