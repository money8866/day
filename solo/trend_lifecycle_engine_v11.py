#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Trend Lifecycle Engine V11 —— A股顶级游资交易系统
====================================================

唯一目标：
  找出"当前唯一可交易主线 + 真龙头 + 最优买点 + 风险边界"

核心决策流程：
  1. 唯一主线识别（资金连续性 + 成交容量 + 扩散能力 + 龙头结构）
  2. 成份股筛选（流动性 ≥ 5亿 + 产业链明确 + 有持续换手）
  3. 唯一真龙识别（板块资金占比 + 结构突破 + 带动能力）
  4. 龙头分层（真龙头1 + 中军1-2 + 补涨龙1-3 + 卡位龙0-1）
  5. 买点系统（entry_type / entry_score / risk_level / position_action）

强约束：
  - 禁止使用涨停数量、人气榜、单日涨幅、短期K线情绪
  - 必须回答：当前买什么、什么时候买、什么时候不该买
"""
import json
import glob
import os
import datetime
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")


def load_constituents(date_str=None):
    if date_str:
        path = os.path.join(CACHE_DIR, "theme3_constituents_%s.json" % date_str)
    else:
        pattern = os.path.join(CACHE_DIR, "theme3_constituents_*.json")
        files = sorted(glob.glob(pattern))
        path = files[-1] if files else None
    if not path or not os.path.exists(path):
        return None, None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data, path


# =============================================================================
# 1. 唯一主线识别（子主题分层加权版）
# =============================================================================

def _score_sub_theme(sub_stocks, total_sub_amount_yi):
    """
    对单个子主题打分（0-100）。

    子主题内部质量评估：
      - 子主题龙头强度（trend>=70 top3平均趋势分）
      - 子主题内资金集中度（成交额top3占总成交的比例）
      - 子主题活跃度（trend>=50股票占比）
      - 子主题短中期动能（5日均涨幅 + MA10向上占比）
    """
    n = len(sub_stocks)
    if n == 0:
        return 0.0, {}

    # 子主题内部指标
    avg_trend = sum(s.get("trend_score", 0) or 0 for s in sub_stocks) / n
    avg_chg5 = sum(s.get("change_5d_pct") or 0 for s in sub_stocks) / n
    n_above = sum(1 for s in sub_stocks if s.get("close_above_ma5") is True)
    n_pos_ma10 = sum(1 for s in sub_stocks if (s.get("ma10_slope_pct") or 0) > 0)
    n_active = sum(1 for s in sub_stocks if (s.get("trend_score") or 0) or 0 >= 50)
    n_high = sum(1 for s in sub_stocks if (s.get("trend_score") or 0) >= 70)

    # 龙头强度：子主题内trend>=70的top3平均分
    top3 = sorted(sub_stocks, key=lambda s: -(s.get("trend_score") or 0))[:3]
    leader_strength = sum(s.get("trend_score", 0) or 0 for s in top3) / len(top3) if top3 else 0

    # 龙头集中度：龙头3只成交占总成交比例（越低=扩散越好）
    top3_amt = sum((s.get("avg_amount_5d") or 0) / 1e8 for s in top3)
    concentration = (top3_amt / total_sub_amount_yi * 100) if total_sub_amount_yi > 0 else 100

    # 子主题得分（六维：短期动量+中期持续性+龙头强度+均趋势+站稳MA5+扩散度）
    # 短期动量(满分30)：5日涨幅强度(放大) + 站稳MA5比例
    short_term = min(100, avg_chg5 * 20) * 0.50 + n_above / n * 100 * 0.50

    # 中期持续性(满分25)：MA10斜率向上比例 + 10日涨幅（需要从调用方传入，这里简化用均趋势）
    mid_term = n_pos_ma10 / n * 100 * 0.60 + avg_trend * 0.40

    score = (
        short_term * 0.30                # 短期动量（1-5日刚启动信号）
        + mid_term * 0.25                 # 中期持续性（10日+健康度）
        + leader_strength * 0.15         # 龙头强度
        + avg_trend * 0.10               # 均趋势分
        + n_active / n * 100 * 0.10     # 活跃股占比
        + min(100, 100 - concentration) * 0.10  # 扩散度
    )

    detail = {
        "leader_strength": round(leader_strength, 1),
        "avg_trend": round(avg_trend, 1),
        "avg_change5_pct": round(avg_chg5, 2),
        "above_ma5_ratio": round(n_above / n * 100, 1),
        "active_ratio": round(n_active / n * 100, 1),
        "high_trend_count": n_high,
        "concentration": round(concentration, 1),
        "sub_amount_yi": round(total_sub_amount_yi, 1),
        "short_term_score": round(short_term, 1),
        "mid_term_score": round(mid_term, 1),
    }
    return round(score, 1), detail


def analyze_primary_mainline(all_themes, categories, primary_map, market_total):
    """
    唯一主线识别（子主题分层加权版）：

      1. 资金连续性：按子主题分层计算，加权汇总
         - 每个子主题单独计算: MA10向上占比、站稳MA5占比、均趋势分
         - 权重 = 该子主题成交额占一级主题总成交的比例
      2. 成交容量：成交额是否进入市场前30%
      3. 产业链扩散：子主题数量 + 活跃子主题数量 + 子主题强度方差（扩散均匀度）
      4. 龙头结构：龙头分布是否跨多个子主题（集中=风险高，分散=健康）

    核心改进：不再把所有成分股当做一个池子，子主题强≠子主题弱时，得分拉开差距。
    """
    cat_metrics = {}

    for cat in categories:
        themes_in_cat = [t for t in all_themes if t.get("top_category") == cat]

        # === Step 1: 收集该一级主题的去重成分股（包含所有匹配股票，不按primary_map过滤）===
        seen = set()
        active_stocks = []
        for t in themes_in_cat:
            for s in t.get("stocks", []):
                code = s.get("ts_code", "")
                if code in seen:
                    continue
                # 移除 primary_map 过滤，包含所有该主题下的股票（去重即可）
                seen.add(code)
                active_stocks.append(s)

        n = len(active_stocks) if active_stocks else 1

        # 全局基础指标（保留用于兼容输出）
        n_pos_ma10 = sum(1 for s in active_stocks if (s.get("ma10_slope_pct") or 0) > 0)
        n_above_ma5 = sum(1 for s in active_stocks if s.get("close_above_ma5") is True)
        avg_trend_global = sum((s.get("trend_score") or 0) for s in active_stocks) / n
        avg_change5_global = sum((s.get("change_5d_pct") or 0) for s in active_stocks) / n
        avg_change10_global = sum((s.get("change_10d_pct") or 0) for s in active_stocks) / n
        avg_combined_global = sum((s.get("combined_score") or 0) for s in active_stocks) / n
        n_high_trend_global = sum(1 for s in active_stocks if (s.get("trend_score") or 0) >= 70)
        total_amount_yi = sum((s.get("avg_amount_5d") or 0) / 1e8 for s in active_stocks)

        # === Step 2: 子主题分层打分 ===
        sub_data = []
        weighted_cap_continuity = 0.0
        weight_sum = 0.0

        for t in themes_in_cat:
            sub_name = t.get("theme_name", "")
            sub_stocks_raw = t.get("stocks", [])

            sub_seen = set()
            sub_unique = []
            for s in sub_stocks_raw:
                code = s.get("ts_code", "")
                if code in sub_seen:
                    continue
                sub_seen.add(code)
                sub_unique.append(s)

            sub_n = len(sub_unique)
            if sub_n == 0:
                continue

            sub_total_amt = sum((s.get("avg_amount_5d") or 0) / 1e8 for s in sub_unique)
            # 权重 = 子主题成交额 / 一级主题总成交额（允许多重归属，但用一级主题总量作分母）
            sub_weight = sub_total_amt / total_amount_yi if total_amount_yi > 0 else 0

            # 子主题质量打分
            sub_score, sub_detail = _score_sub_theme(sub_unique, sub_total_amt)

            # 子主题内部资金连续性指标
            sub_pos_ma10 = sum(1 for s in sub_unique if (s.get("ma10_slope_pct") or 0) > 0)
            sub_above_ma5 = sum(1 for s in sub_unique if s.get("close_above_ma5") is True)
            sub_avg_trend = sum((s.get("trend_score") or 0) for s in sub_unique) / sub_n

            sub_cap = (
                sub_pos_ma10 / sub_n * 100 * 0.35
                + sub_above_ma5 / sub_n * 100 * 0.35
                + sub_avg_trend * 0.30
            )

            weighted_cap_continuity += sub_cap * sub_weight
            weight_sum += sub_weight

            # 子主题内龙头股
            top_leaders = sorted(sub_unique, key=lambda s: -(s.get("trend_score") or 0))[:3]

            sub_data.append({
                "sub_theme": sub_name,
                "sub_score": sub_score,
                "sub_weight_pct": round(sub_weight * 100, 1),
                "sub_amount_yi": round(sub_total_amt, 1),
                "capital_continuity": round(sub_cap, 1),
                "above_ma5_ratio": sub_detail.get("above_ma5_ratio", 0),
                "avg_trend": sub_detail.get("avg_trend", 0),
                "avg_change5_pct": sub_detail.get("avg_change5_pct", 0),
                "high_trend_count": sub_detail.get("high_trend_count", 0),
                "leader_strength": sub_detail.get("leader_strength", 0),
                "concentration": sub_detail.get("concentration", 0),
                "top_leaders": [
                    {
                        "name": ls.get("name", ""),
                        "ts_code": ls.get("ts_code", ""),
                        "trend_score": ls.get("trend_score", 0),
                        "combined_score": round(ls.get("combined_score") or 0, 1),
                        "amount_yi": round((ls.get("avg_amount_5d") or 0) / 1e8, 1),
                    }
                    for ls in top_leaders
                ]
            })

        # 归一化加权资金连续性
        capital_continuity = weighted_cap_continuity / weight_sum if weight_sum > 0 else 0

        # === Step 2.5: 时间维度评分 - 短期动量(1-5日) vs 中期持续性(20日+/60日++) ===
        # 短期动量评分：5日涨幅强度 + 站稳MA5比例 + 高趋势股比例
        short_term_score = (
            min(100, avg_change5_global * 20) * 0.50   # 5日涨幅强度（×20放大后截断）
            + n_above_ma5 / n * 100 * 0.30              # 站稳MA5比例（信号确认）
            + min(100, n_high_trend_global / n * 200) * 0.20  # 高趋势股比例（强度广度）
        )

        # 中期持续性评分（使用20日/60日数据，不再依赖10日）
        # avg_change20 = sum((s.get("change_20d_pct") or 0) for s in active_stocks) / n
        avg_change20_global = sum((s.get("change_20d_pct") or 0) for s in active_stocks) / n
        avg_change60_global = sum((s.get("change_60d_pct") or 0) for s in active_stocks) / n
        n_above_ma20 = sum(1 for s in active_stocks if s.get("close_above_ma20") is True)
        n_above_ma60 = sum(1 for s in active_stocks if s.get("close_above_ma60") is True)
        avg_ma20_slope = sum((s.get("ma20_slope_pct") or 0) for s in active_stocks) / n
        avg_ma60_slope = sum((s.get("ma60_slope_pct") or 0) for s in active_stocks) / n

        mid_term_score = (
            min(100, avg_change20_global * 5 + 50) * 0.20   # 20日涨幅（以50为中性基准）
            + min(100, avg_change60_global * 2.5 + 50) * 0.20  # 60日涨幅（长期趋势）
            + n_above_ma20 / n * 100 * 0.20                     # 站稳MA20比例（中期结构）
            + n_above_ma60 / n * 100 * 0.15                     # 站稳MA60比例（长期结构）
            + min(100, avg_ma20_slope * 5 + 50) * 0.15        # MA20斜率
            + avg_trend_global * 0.10                           # 均趋势分（综合质量）
        )

        # 加速度信号：5日涨幅 vs 10日涨幅的关系（短期是否在加速）
        # avg_chg5 > avg_chg10 表示短期更强（加速上行或修复反弹）
        acceleration_signal = avg_change5_global - avg_change10_global

        # === Step 3: 扩散能力（子主题强度分布，0-100分）===
        n_subs = len(sub_data)
        if n_subs > 0:
            sub_scores = [s["sub_score"] for s in sub_data]
            # 权重 = 子主题成交额 / 一级主题总成交额（归一化后相加=1）
            total_weight = sum(s["sub_weight_pct"] for s in sub_data) / 100

            # 加权子主题得分（反映整体质量，满分100）
            weighted_sub_score = sum(sc * w for sc, w in zip(
                sub_scores,
                [s["sub_weight_pct"] / 100 / total_weight for s in sub_data]
            )) if total_weight > 0 else 0

            # 扩散均匀度（子主题得分标准差，越低=扩散越均匀=越好，满分100）
            mean_sub = sum(sub_scores) / n_subs
            variance = sum((sc - mean_sub) ** 2 for sc in sub_scores) / n_subs
            std_sub = variance ** 0.5
            uniformity_score = max(0, 100 - std_sub * 3)

            # 活跃子主题数
            active_subs = sum(1 for sc in sub_scores if sc >= 50)

            # 扩散广度（有效子主题数/5，满分100）
            breadth_score = min(100, n_subs / 5 * 100)

            diffusion_score = (
                weighted_sub_score * 0.35    # 加权子主题强度（满分35）
                + uniformity_score * 0.30     # 均匀度（满分30）
                + active_subs / n_subs * 100 * 0.20  # 活跃比例（满分20）
                + breadth_score * 0.15      # 扩散广度（满分15）
            )
        else:
            weighted_sub_score = uniformity_score = diffusion_score = 0
            active_subs = 0

        # === Step 4: 龙头结构（跨子主题分布）===
        top5_amount = sorted(active_stocks, key=lambda s: -(s.get("avg_amount_5d") or 0))[:5]
        top5_trend_avg = sum((s.get("trend_score") or 0) for s in top5_amount) / len(top5_amount) if top5_amount else 0
        top5_combined_avg = sum((s.get("combined_score") or 0) for s in top5_amount) / len(top5_amount) if top5_amount else 0

        # 龙头所在子主题数量（越多=越分散=越健康）
        sub_names_of_top5 = set()
        for s in top5_amount:
            for sd in sub_data:
                if any(ls.get("ts_code", "") == s.get("ts_code", "") for ls in sd.get("top_leaders", [])):
                    sub_names_of_top5.add(sd["sub_theme"])
                    break

        leader_diversity = len(sub_names_of_top5) / n_subs * 100 if n_subs > 0 else 0

        leader_structure = (
            top5_trend_avg * 0.40
            + top5_combined_avg * 0.30
            + leader_diversity * 0.20
            + min(100, (top5_amount[0].get("avg_amount_5d") or 0) / 1e8 / 300 * 100) * 0.10
            if top5_amount else 0
        )

        # === Step 5: 成交容量 ===
        market_share = total_amount_yi / market_total * 100 if market_total > 0 else 0
        if market_share >= 10:
            capacity_score = 100
        elif market_share >= 5:
            capacity_score = 90
        elif market_share >= 3:
            capacity_score = 80
        elif market_share >= 1.5:
            capacity_score = 70
        elif market_share >= 0.8:
            capacity_score = 60
        elif market_share >= 0.4:
            capacity_score = 50
        else:
            capacity_score = 30

        # === Step 6: 综合主线评分（含时间维度）===
        mainline_score = (
            short_term_score * 0.25      # 短期动量：1-5日刚启动信号
            + mid_term_score * 0.25       # 中期持续性：10日+趋势健康度
            + capital_continuity * 0.15  # 资金连续性：子主题加权后的资金强度
            + capacity_score * 0.10      # 成交容量：容纳大资金能力
            + diffusion_score * 0.15    # 扩散能力：子主题强度+均匀度
            + leader_structure * 0.10   # 龙头结构：跨子主题分散度
        )

        cat_metrics[cat] = {
            "mainline_score": round(mainline_score, 1),
            "capital_continuity": round(capital_continuity, 1),
            "capacity_score": round(capacity_score, 1),
            "diffusion_score": round(diffusion_score, 1),
            "leader_structure": round(leader_structure, 1),
            "short_term_score": round(short_term_score, 1),
            "mid_term_score": round(mid_term_score, 1),
            "acceleration_signal": round(acceleration_signal, 1),
            "avg_change20_pct": round(avg_change20_global, 1),
            "avg_change60_pct": round(avg_change60_global, 1),
            "above_ma20_ratio": round(n_above_ma20 / n * 100, 1),
            "above_ma60_ratio": round(n_above_ma60 / n * 100, 1),
            "avg_ma20_slope_pct": round(avg_ma20_slope, 2),
            "avg_ma60_slope_pct": round(avg_ma60_slope, 2),
            "n_stocks": n,
            "total_amount_yi": round(total_amount_yi, 1),
            "market_share_pct": round(market_share, 1),
            "pos_ma10_ratio": round(n_pos_ma10 / n * 100, 1),
            "above_ma5_ratio": round(n_above_ma5 / n * 100, 1),
            "avg_trend": round(avg_trend_global, 1),
            "avg_change5_pct": round(avg_change5_global, 1),
            "avg_change10_pct": round(avg_change10_global, 1),
            "avg_combined": round(avg_combined_global, 1),
            "n_high_trend": n_high_trend_global,
            "high_trend_ratio": round(n_high_trend_global / n * 100, 1),
            "n_active_subs": active_subs,
            "n_total_subs": n_subs,
            "sub_themes": sorted(sub_data, key=lambda x: -x["sub_score"]),
            "top5_trend": round(top5_trend_avg, 1),
            "top5_combined": round(top5_combined_avg, 1),
            "top_stocks": [
                {
                    "name": s.get("name", ""),
                    "ts_code": s.get("ts_code", ""),
                    "amount_yi": round((s.get("avg_amount_5d") or 0) / 1e8, 1),
                    "trend_score": s.get("trend_score", 0),
                    "combined_score": round(s.get("combined_score") or 0, 1),
                    "change_5d_pct": round(s.get("change_5d_pct") or 0, 1),
                    "change_10d_pct": round(s.get("change_10d_pct") or 0, 1),
                    "ma10_slope_pct": round(s.get("ma10_slope_pct") or 0, 2),
                    "close_above_ma5": s.get("close_above_ma5") is True,
                }
                for s in top5_amount
            ],
        }

    # 排序：主线综合分
    sorted_cats = sorted(cat_metrics.items(), key=lambda x: -x[1]["mainline_score"])

    # 唯一主线判定规则：
    #   - mainline_score 需 > 60
    #   - 与第二名拉开足够差距（相对优势 > 5%）
    #   - 否则判定为"震荡市，无明确主线"
    primary = None
    other_classifications = []

    if sorted_cats and sorted_cats[0][1]["mainline_score"] >= 50:
        primary = sorted_cats[0][0]
        primary_score = sorted_cats[0][1]["mainline_score"]

        # 其他主题分类（综合短期+中期判断）
        for cat, m in sorted_cats[1:]:
            score_gap = primary_score - m["mainline_score"]

            # 判定其他主题类型（优先级：中期强势 > 轮动支线 > 反弹结构 > 退潮修复）
            # 1. 中期强势：60日涨幅>0 且 站上MA60比例>=40% 且 中期分>=45
            if m.get("avg_change60_pct", 0) > 0 and m.get("above_ma60_ratio", 0) >= 40 and m.get("mid_term_score", 0) >= 45:
                ctype = "中期强势"
            # 2. 轮动支线：资金连续性好 且 主线分>=45
            elif m["capital_continuity"] >= 50 and m["mainline_score"] >= 45:
                ctype = "轮动支线"
            # 3. 反弹结构：短期有反弹迹象（5日涨>0 且 站上MA5比例>=40%）
            elif m["avg_change5_pct"] > 0 and m["above_ma5_ratio"] >= 40:
                ctype = "反弹结构"
            # 4. 退潮修复：资金持续流出，无明确结构
            else:
                ctype = "退潮修复"
            other_classifications.append({"theme": cat, "type": ctype, "score": m["mainline_score"]})

    return primary, cat_metrics, sorted_cats, other_classifications


# =============================================================================
# 2. 成份股筛选
# =============================================================================

def filter_valid_constituents(category, all_themes, primary_map):
    """
    成份股筛选系统（关键新增）：

    ❌ 剔除：
      - 流动性不足（成交额<5亿）
      - 无行业归属（纯概念）—— industry_match == False
      - 无产业链位置（trend_score < 30 或 combined_score < 30）
      - 单日脉冲（change_5d_pct > 15 但 ma10_slope < 0 且 close_above_ma5 == False）

    ✔ 保留：
      - 产业链明确（industry_match == True）
      - 可被机构/游资共同交易（combined_score >= 40）
      - 有持续换手结构（avg_amount_5d >= 5亿 AND trend_score >= 40）
    """
    themes_in_cat = [t for t in all_themes if t.get("top_category") == category]
    seen = set()
    candidates = []

    for t in themes_in_cat:
        for s in t.get("stocks", []):
            code = s.get("ts_code", "")
            if code in seen:
                continue
            if primary_map.get(code) != category:
                continue
            seen.add(code)

            amt_yi = (s.get("avg_amount_5d") or 0) / 1e8
            trend = s.get("trend_score", 0) or 0
            combined = s.get("combined_score", 0) or 0
            ind_match = s.get("industry_match", False)
            change5 = s.get("change_5d_pct") or 0
            ma10_slope = s.get("ma10_slope_pct") or 0
            above_ma5 = s.get("close_above_ma5") is True
            change10 = s.get("change_10d_pct") or 0

            # 剔除条件
            reasons = []
            if amt_yi < 5:
                reasons.append(f"流动性不足({amt_yi:.1f}亿<5亿)")
            if ind_match is False:
                reasons.append("无行业归属")
            if trend < 30 and combined < 30:
                reasons.append("无产业链位置")
            # 单日脉冲：5日大涨但MA10向下 + 未站稳MA5
            if change5 > 15 and ma10_slope < 0 and not above_ma5:
                reasons.append("疑似单日脉冲")

            if reasons:
                continue

            # 通过筛选
            candidates.append({
                "ts_code": code,
                "name": s.get("name", ""),
                "amount_yi": round(amt_yi, 1),
                "trend_score": trend,
                "combined_score": combined,
                "change_5d_pct": round(change5, 1),
                "change_10d_pct": round(change10, 1),
                "ma10_slope_pct": round(ma10_slope, 2),
                "close_above_ma5": above_ma5,
                "industry_match": ind_match,
                "role": s.get("role", ""),
                "sub_theme": t.get("theme_name", ""),
                "total_mv_wan": s.get("total_mv_wan", 0),
            })

    # 按成交额（交易容量）排序
    candidates.sort(key=lambda x: -x["amount_yi"])
    return candidates


# =============================================================================
# 3. 唯一真龙识别
# =============================================================================

def identify_leader(valid_stocks, category, cat_metrics):
    """
    唯一真龙识别系统：

    1. 资金维度：板块内资金占比最高、趋势分最高
    2. 结构维度：突破前高（change_10d > 0 + above_ma5），回撤不破均线
    3. 市场地位：板块成交额 Top1/Top2，combined_score 高

    ❌ 不允许：
      - 小市值纯情绪股（成交额<10亿）
      - 单日爆量票（ma10_slope <= 0 但 change_5d > 15）
    """
    if not valid_stocks:
        return None

    # 进一步过滤真龙候选（更严格）：
    #   - 成交额 >= 10亿（大资金可交易）
    #   - 趋势分 >= 60（有趋势）
    #   - change_10d_pct > 0（中期向上）
    #   - combined_score >= 50（机构认可）
    #   - close_above_ma5 == True（站稳MA5）
    candidates = [
        s for s in valid_stocks
        if s["amount_yi"] >= 10
        and s["trend_score"] >= 60
        and s["change_10d_pct"] > 0
        and s["combined_score"] >= 50
        and s["close_above_ma5"] is True
    ]

    if not candidates:
        # 放宽条件：允许 combined_score >= 40
        candidates = [
            s for s in valid_stocks
            if s["amount_yi"] >= 8
            and s["trend_score"] >= 50
            and s["change_10d_pct"] > -5
            and s["combined_score"] >= 40
        ]

    if not candidates:
        return None

    # 真龙评分：资金占比(30%) + 趋势强度(25%) + 综合认可(20%) + MA10斜率(15%) + 中期涨幅(10%)
    total_amt = sum(s["amount_yi"] for s in candidates)
    for s in candidates:
        cap_ratio_score = min(100, s["amount_yi"] / total_amt * 100 * 3)  # 资金占比
        trend_score_val = s["trend_score"]
        combined_val = s["combined_score"]
        ma10_score = min(100, max(0, s["ma10_slope_pct"] * 20 + 50))
        change10_score = min(100, max(0, s["change_10d_pct"] * 5 + 50))

        s["_leader_score"] = round(
            cap_ratio_score * 0.30
            + trend_score_val * 0.25
            + combined_val * 0.20
            + ma10_score * 0.15
            + change10_score * 0.10,
            1
        )

    # 选最高分
    candidates.sort(key=lambda x: -x["_leader_score"])
    return candidates[0]


# =============================================================================
# 4. 龙头分层系统
# =============================================================================

def classify_leaders(valid_stocks, primary_leader, category):
    """
    龙头分层体系：

    1. 真龙头（1只）：来自 identify_leader 的唯一结果
    2. 趋势中军（1-2只）：高成交额 + 稳定趋势（combined_score >= 60）
    3. 补涨龙（1-3只）：中等成交额 + 趋势分 >= 50 + change_10d 中等
    4. 卡位龙（0-1只）：高趋势分 + 行业匹配度高 + 与真龙头有产业关联

    每个位置 entry_score 不得=100（避免绝对化）
    """
    results = {
        "true_leader": None,
        "trend_core_companies": [],
        "followup_leaders": [],
        "rotation_leader": None,
    }

    if not valid_stocks:
        return results

    seen_codes = set()

    # 1. 真龙头
    if primary_leader:
        results["true_leader"] = {
            "name": primary_leader["name"],
            "ts_code": primary_leader["ts_code"],
            "amount_yi": primary_leader["amount_yi"],
            "trend_score": primary_leader["trend_score"],
            "combined_score": primary_leader["combined_score"],
            "sub_theme": primary_leader.get("sub_theme", ""),
        }
        seen_codes.add(primary_leader["ts_code"])

    # 2. 趋势中军（1-2只）：高成交额+稳定趋势+非真龙头
    core_candidates = [
        s for s in valid_stocks
        if s["ts_code"] not in seen_codes
        and s["amount_yi"] >= 20
        and s["combined_score"] >= 55
        and s["trend_score"] >= 50
    ]
    core_candidates.sort(key=lambda s: -s["amount_yi"])

    for s in core_candidates[:2]:
        results["trend_core_companies"].append({
            "name": s["name"],
            "ts_code": s["ts_code"],
            "amount_yi": s["amount_yi"],
            "trend_score": s["trend_score"],
            "combined_score": s["combined_score"],
            "sub_theme": s.get("sub_theme", ""),
        })
        seen_codes.add(s["ts_code"])

    # 若中军不足2只，放宽
    if len(results["trend_core_companies"]) < 1:
        extra = [s for s in valid_stocks
                 if s["ts_code"] not in seen_codes
                 and s["amount_yi"] >= 10
                 and s["combined_score"] >= 50]
        extra.sort(key=lambda s: -s["amount_yi"])
        for s in extra[:1]:
            results["trend_core_companies"].append({
                "name": s["name"],
                "ts_code": s["ts_code"],
                "amount_yi": s["amount_yi"],
                "trend_score": s["trend_score"],
                "combined_score": s["combined_score"],
                "sub_theme": s.get("sub_theme", ""),
            })
            seen_codes.add(s["ts_code"])

    # 3. 补涨龙（1-3只）：中等成交额 + 有趋势但不极端 + 行业匹配
    followup_candidates = [
        s for s in valid_stocks
        if s["ts_code"] not in seen_codes
        and 5 <= s["amount_yi"] < 50
        and 40 <= s["trend_score"] < 80
        and s["change_10d_pct"] > -5
    ]
    # 排序：趋势分 + 行业匹配度优先
    followup_candidates.sort(
        key=lambda s: -(s["trend_score"] + s["combined_score"])
    )

    for s in followup_candidates[:3]:
        results["followup_leaders"].append({
            "name": s["name"],
            "ts_code": s["ts_code"],
            "amount_yi": s["amount_yi"],
            "trend_score": s["trend_score"],
            "combined_score": s["combined_score"],
            "sub_theme": s.get("sub_theme", ""),
        })
        seen_codes.add(s["ts_code"])

    # 4. 卡位龙（0-1只）：与真龙头有产业关联（同子主题或产业链相邻）+ 高趋势
    if primary_leader:
        main_sub = primary_leader.get("sub_theme", "")
        rotation_candidates = [
            s for s in valid_stocks
            if s["ts_code"] not in seen_codes
            and (s.get("sub_theme", "") == main_sub)  # 同子主题 → 卡位
            and s["trend_score"] >= 60
            and s["amount_yi"] >= 5
        ]
        rotation_candidates.sort(key=lambda s: -s["trend_score"])
        if rotation_candidates:
            rs = rotation_candidates[0]
            results["rotation_leader"] = {
                "name": rs["name"],
                "ts_code": rs["ts_code"],
                "amount_yi": rs["amount_yi"],
                "trend_score": rs["trend_score"],
                "combined_score": rs["combined_score"],
                "sub_theme": rs.get("sub_theme", ""),
            }
            seen_codes.add(rs["ts_code"])

    return results


# =============================================================================
# 5. 买点系统
# =============================================================================

def compute_entry_analysis(stock_info, category, lifecycle_stage, is_primary_mainline):
    """
    买点分析系统（核心升级）：

    entry_type 判定：
      - 首板：change_5d > 5% 且 ma10_slope > 0 且 trend_score > 60
      - 突破：change_10d > 10% 且 close_above_ma5 == True 且 trend_score >= 70
      - 回踩：change_5d < 0 但 change_10d > 0 且 close_above_ma5 == True
      - 二波：ma10_slope > 0 且 trend_score >= 60 且 change_5d 轻微回撤
      - 观察：其他情况

    entry_score（0-95，禁止100）：
      - 主线主题加分
      - 资金持续性加分（MA10斜率、trend_score、combined_score）
      - 容量加分

    risk_level：
      - low: 中军 + 站稳MA5 + MA10强向上
      - medium: 龙头 + 趋势稳定
      - high: 补涨/卡位 + 波动大

    position_action：
      - 试仓：首次启动或震荡
      - 加仓：主线确认 + 结构稳定
      - 观察：信号不明确
      - 回避：退潮期 / 资金流出
    """
    trend = stock_info.get("trend_score", 0) or 0
    combined = stock_info.get("combined_score", 0) or 0
    ma10 = stock_info.get("ma10_slope_pct", 0) or 0
    change5 = stock_info.get("change_5d_pct", 0) or 0
    change10 = stock_info.get("change_10d_pct", 0) or 0
    above_ma5 = stock_info.get("close_above_ma5") is True
    amount = stock_info.get("amount_yi", 0) or 0
    role = stock_info.get("role_str", "")

    # entry_type 判定
    # 二波：MA10强向上+趋势高，但短期有回调（change_5d < 5%）
    if ma10 > 1 and trend >= 70 and -3 <= change5 <= 8 and above_ma5:
        entry_type = "二波"
    # 突破：中期趋势明确+站稳MA5+高趋势分
    elif change10 > 10 and above_ma5 and trend >= 70:
        entry_type = "突破"
    # 首板/启动：短期强势+趋势成立
    elif change5 > 5 and ma10 > 0 and trend >= 60:
        entry_type = "首板"
    # 回踩：短期回调但中期向上
    elif change5 < 0 and change10 > 0 and above_ma5 and trend >= 50:
        entry_type = "回踩"
    else:
        entry_type = "观察"

    # entry_score（0-95）
    base = trend * 0.30 + combined * 0.25 + min(100, ma10 * 20 + 50) * 0.20
    capacity = min(100, amount / 200 * 100) * 0.15
    mainline_bonus = 10 if is_primary_mainline else 0
    entry_score = round(min(95, max(10, base + capacity + mainline_bonus)), 0)

    # risk_level
    if role == "趋势中军" and above_ma5 and ma10 > 0.5:
        risk_level = "low"
    elif role in ("真龙头", "趋势龙头") and above_ma5:
        risk_level = "medium"
    elif role in ("补涨龙", "卡位龙"):
        risk_level = "high"
    elif not above_ma5 or ma10 < 0:
        risk_level = "high"
    else:
        risk_level = "medium"

    # position_action
    if lifecycle_stage == "退潮期":
        position_action = "回避"
    elif lifecycle_stage == "启动期":
        position_action = "试仓"
    elif lifecycle_stage in ("主升期", "高潮期") and is_primary_mainline:
        if entry_type in ("突破", "二波") and entry_score >= 60:
            position_action = "加仓"
        elif entry_type == "回踩":
            position_action = "试仓"
        else:
            position_action = "观察"
    elif lifecycle_stage == "分歧期":
        position_action = "观察"
    else:
        if entry_score >= 55 and is_primary_mainline:
            position_action = "试仓"
        else:
            position_action = "观察"

    # 强制：退潮期一律回避
    if lifecycle_stage == "退潮期":
        position_action = "回避"
        entry_score = max(10, entry_score - 20)

    return {
        "entry_type": entry_type,
        "entry_score": entry_score,
        "risk_level": risk_level,
        "position_action": position_action,
    }


# =============================================================================
# 6. 生命周期判定（简化版）
# =============================================================================

def determine_lifecycle_v11(m):
    """
    简化版生命周期，基于资金+结构：

    - 退潮期：资金连续性 < 40 且 扩散 < 30
    - 启动期：资金连续性 40-55 且 扩散 < 50
    - 主升期：资金连续性 >= 55 且 扩散 >= 50 且 龙头结构 >= 50
    - 高潮期：资金连续性 >= 65 且 扩散 >= 60 且 5日平均涨幅 >= 3%
    - 分歧期：资金 50-65 但 扩散 < 50
    """
    cc = m["capital_continuity"]
    diff = m["diffusion_score"]
    leader = m["leader_structure"]
    avg_chg5 = m["avg_change5_pct"]

    if cc < 40 and diff < 30:
        return "退潮期"
    elif cc >= 65 and diff >= 60 and avg_chg5 >= 3:
        return "高潮期"
    elif cc >= 55 and diff >= 50 and leader >= 50:
        return "主升期"
    elif 40 <= cc < 55 and diff < 50:
        return "启动期"
    elif 50 <= cc < 65 and diff < 50:
        return "分歧期"
    elif cc >= 50 and diff < 40:
        return "分歧期"
    else:
        return "启动期" if cc >= 40 else "退潮期"


# =============================================================================
# 6.5 爆发潜力分（新增模块，V2 - 相对排名版）
# =============================================================================

def _percentile_rank(values):
    """将一列数值转换为百分位排名（0-100）。"""
    n = len(values)
    if n == 0:
        return []
    ranked = sorted(range(n), key=lambda i: values[i])
    return [round((ranked.index(i) + 1) / n * 100, 1) for i in range(n)]


def compute_breakout_score(all_themes, categories, primary_map, cat_metrics):
    """
    爆发潜力分 V2：识别"资金悄悄进场但还没被市场发现"的主题
    ——改为相对排名打分，避免绝对阈值导致所有主题都满分

    算法（满分100）：
      Step 1: 计算所有主题的原始指标
      Step 2: 将8项指标各自转为百分位排名（0-100）
      Step 3: 按权重乘积得到最终分

      权重：
        1) 站稳MA5比例   权重 0.20 → 最高20分
        2) 高趋势股占比  权重 0.18 → 最高18分
        3) 5日平均涨幅   权重 0.18 → 最高18分
        4) MA10斜率      权重 0.14 → 最高14分
        5) 平均趋势分    权重 0.10 → 最高10分
        6) 5日上涨比例   权重 0.08 → 最高8分
        7) 资金回流      权重 0.07 → 最高7分   (5日涨幅差值)
        8) 成交额集中度  权重 0.05 → 最高5分   (大票占比)

    评分等级：🟢≥70 高潜力 / 🟡50-70 中潜力 / 🟠35-50 低潜力 / 🔴<35 不推荐
    """
    # ========== Step 1: 收集所有主题的原始指标 ==========
    raw_data = {}  # cat -> raw metrics dict

    for cat in categories:
        themes_in_cat = [t for t in all_themes if t.get("top_category") == cat]
        seen = set()
        cat_stocks = []
        for t in themes_in_cat:
            for s in t.get("stocks", []):
                code = s.get("ts_code", "")
                if code in seen or primary_map.get(code) != cat:
                    continue
                seen.add(code)
                cat_stocks.append(s)

        n = len(cat_stocks)
        if n == 0:
            continue

        n_above_ma5 = sum(1 for s in cat_stocks if s.get("close_above_ma5") is True)
        n_high_trend = sum(1 for s in cat_stocks if (s.get("trend_score") or 0) >= 70)
        n_pos_ma10 = sum(1 for s in cat_stocks if (s.get("ma10_slope_pct") or 0) > 0)
        n_up5 = sum(1 for s in cat_stocks if (s.get("change_5d_pct") or 0) > 0)
        n_large = sum(1 for s in cat_stocks if (s.get("avg_amount_5d") or 0) / 1e8 >= 10)

        avg_chg5 = sum(s.get("change_5d_pct") or 0 for s in cat_stocks) / n
        avg_chg10 = sum(s.get("change_10d_pct") or 0 for s in cat_stocks) / n
        avg_ma10 = sum(s.get("ma10_slope_pct") or 0 for s in cat_stocks) / n
        avg_trend = sum(s.get("trend_score") or 0 for s in cat_stocks) / n

        raw_data[cat] = {
            "stocks": cat_stocks,
            "themes_in_cat": themes_in_cat,
            "n": n,
            "above_ratio": n_above_ma5 / n * 100,
            "high_trend_ratio": n_high_trend / n * 100,
            "avg_chg5": avg_chg5,
            "avg_chg10": avg_chg10,
            "avg_ma10": avg_ma10,
            "avg_trend": avg_trend,
            "up_ratio": n_up5 / n * 100,
            "reversal_diff": avg_chg5 - avg_chg10,
            "large_cap_ratio": n_large / n * 100,
            "positive_ma10_ratio": n_pos_ma10 / n * 100,
        }

    valid_cats = list(raw_data.keys())
    N = len(valid_cats)

    # ========== Step 2: 计算每项指标的百分位排名 ==========
    pct = {cat: {} for cat in valid_cats}
    metric_keys = [
        "above_ratio",      # 1
        "high_trend_ratio", # 2
        "avg_chg5",         # 3
        "avg_ma10",         # 4
        "avg_trend",        # 5
        "up_ratio",         # 6
        "reversal_diff",    # 7
        "large_cap_ratio",  # 8
    ]

    for key in metric_keys:
        values = [raw_data[c][key] for c in valid_cats]
        ranks = _percentile_rank(values)
        for i, cat in enumerate(valid_cats):
            pct[cat][key] = ranks[i]

    # ========== Step 3: 计算爆发潜力分 ==========
    # 权重配置
    WEIGHTS = {
        "above_ratio":     0.20,
        "high_trend_ratio": 0.18,
        "avg_chg5":        0.18,
        "avg_ma10":        0.14,
        "avg_trend":       0.10,
        "up_ratio":        0.08,
        "reversal_diff":   0.07,
        "large_cap_ratio": 0.05,
    }

    results = []

    for cat in valid_cats:
        d = raw_data[cat]
        rd = pct[cat]

        # 8项加权得分（满分100）
        components = {k: round(rd[k] * w, 2) for k, w in WEIGHTS.items()}
        score = round(sum(components.values()), 1)

        # 等级
        if score >= 70:
            level = "🟢 高潜力"
        elif score >= 50:
            level = "🟡 中潜力"
        elif score >= 35:
            level = "🟠 低潜力"
        else:
            level = "🔴 不推荐"

        # 子主题分析
        sub_theme_data = []
        for t in d["themes_in_cat"]:
            sub = t.get("theme_name", "")
            sub_stocks = t.get("stocks", [])
            if not sub_stocks:
                continue
            sub_n = len(sub_stocks)
            sub_above = sum(1 for s in sub_stocks if s.get("close_above_ma5") is True) / sub_n * 100
            sub_avg_trend = sum(s.get("trend_score") or 0 for s in sub_stocks) / sub_n
            sub_avg_chg5 = sum(s.get("change_5d_pct") or 0 for s in sub_stocks) / sub_n
            sub_total_amt = sum((s.get("avg_amount_5d") or 0) / 1e8 for s in sub_stocks)

            top_individuals = sorted(
                [s for s in sub_stocks if (s.get("trend_score") or 0) >= 70 and (s.get("avg_amount_5d") or 0) / 1e8 >= 3],
                key=lambda s: -(s.get("trend_score") or 0)
            )[:5]

            sub_theme_data.append({
                "sub_theme": sub,
                "stock_count": sub_n,
                "above_ma5_ratio": round(sub_above, 1),
                "avg_trend_score": round(sub_avg_trend, 1),
                "avg_change5_pct": round(sub_avg_chg5, 2),
                "total_amount_yi": round(sub_total_amt, 1),
                "top_individuals": [
                    {
                        "name": s.get("name", ""),
                        "ts_code": s.get("ts_code", ""),
                        "trend_score": s.get("trend_score", 0),
                        "combined_score": round(s.get("combined_score", 0), 1),
                        "change_5d_pct": round(s.get("change_5d_pct") or 0, 1),
                        "change_10d_pct": round(s.get("change_10d_pct") or 0, 1),
                        "amount_yi": round((s.get("avg_amount_5d") or 0) / 1e8, 1),
                        "ma10_slope_pct": round(s.get("ma10_slope_pct") or 0, 2),
                        "close_above_ma5": s.get("close_above_ma5") is True,
                    }
                    for s in top_individuals
                ]
            })

        # Top爆发力个股
        seen_codes = set()
        top_breakout_stocks = []
        for s in sorted(d["stocks"], key=lambda x: -(x.get("trend_score") or 0)):
            code = s.get("ts_code", "")
            if code in seen_codes:
                continue
            if (s.get("trend_score") or 0) < 60:
                continue
            if (s.get("avg_amount_5d") or 0) / 1e8 < 3:
                continue
            seen_codes.add(code)
            top_breakout_stocks.append({
                "name": s.get("name", ""),
                "ts_code": s.get("ts_code", ""),
                "trend_score": s.get("trend_score", 0),
                "combined_score": round(s.get("combined_score", 0), 1),
                "change_5d_pct": round(s.get("change_5d_pct") or 0, 1),
                "change_10d_pct": round(s.get("change_10d_pct") or 0, 1),
                "amount_yi": round((s.get("avg_amount_5d") or 0) / 1e8, 1),
                "ma10_slope_pct": round(s.get("ma10_slope_pct") or 0, 2),
                "close_above_ma5": s.get("close_above_ma5") is True,
            })
            if len(top_breakout_stocks) >= 8:
                break

        total_amt_yi = sum((s.get("avg_amount_5d") or 0) / 1e8 for s in d["stocks"])

        results.append({
            "top_category": cat,
            "breakout_score": score,
            "level": level,
            "stock_count": d["n"],
            "total_amount_yi": round(total_amt_yi, 1),
            "avg_trend_score": round(d["avg_trend"], 1),
            "avg_change5_pct": round(d["avg_chg5"], 2),
            "avg_change10_pct": round(d["avg_chg10"], 2),
            "avg_ma10_slope_pct": round(d["avg_ma10"], 2),
            "above_ma5_ratio": round(d["above_ratio"], 1),
            "high_trend_ratio": round(d["high_trend_ratio"], 1),
            "up_5d_ratio": round(d["up_ratio"], 1),
            "positive_ma10_ratio": round(d["positive_ma10_ratio"], 1),
            "score_components": {k: round(rd[k] * w, 1) for k, w in WEIGHTS.items()},
            "sub_themes": sub_theme_data,
            "top_breakout_stocks": top_breakout_stocks,
        })

    results.sort(key=lambda x: -x["breakout_score"])
    return results


# =============================================================================
# 主函数
# =============================================================================

def main():
    data, path = load_constituents()
    if not data:
        print(json.dumps({"error": "未找到成分股数据"}, ensure_ascii=False, indent=2))
        return

    trade_date = data.get("trade_date", str(datetime.datetime.now().strftime("%Y%m%d")))
    all_themes = data.get("themes", [])

    # 构建去重映射
    categories = list(set(t.get("top_category", "其他") for t in all_themes))
    primary_map = {}
    primary_scores = {}

    # 第一步：计算每个一级主题的去重成交额
    for cat in categories:
        themes_in_cat = [t for t in all_themes if t.get("top_category") == cat]
        seen = set()
        amt = 0
        for t in themes_in_cat:
            for s in t.get("stocks", []):
                code = s.get("ts_code", "")
                if code not in seen:
                    seen.add(code)
                    amt += (s.get("avg_amount_5d") or 0) / 1e8
        primary_scores[cat] = amt

    # 第二步：为每只股票分配主导主题
    stock_cats = defaultdict(list)
    for t in all_themes:
        cat = t.get("top_category", "其他")
        for s in t.get("stocks", []):
            code = s.get("ts_code", "")
            if code and cat not in stock_cats[code]:
                stock_cats[code].append(cat)

    for code, cats in stock_cats.items():
        if len(cats) == 1:
            primary_map[code] = cats[0]
        else:
            best = max(cats, key=lambda c: primary_scores.get(c, 0))
            primary_map[code] = best

    market_total = sum(primary_scores.values())

    # ========== 1. 唯一主线识别 ==========
    primary, cat_metrics, sorted_cats, other_classifications = \
        analyze_primary_mainline(all_themes, categories, primary_map, market_total)

    # ========== 2. 对主线主题进行详细分析 ==========
    if not primary:
        # 爆发潜力分（无主线时也计算）
        breakout_results = compute_breakout_score(all_themes, categories, primary_map, cat_metrics)
        top5_breakout = breakout_results[:5]

        output = {
            "trade_date": trade_date,
            "engine": "V11",
            "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "primary_mainline": None,
            "market_total_amount_yi": round(market_total, 1),
            "breakout_themes_top5": top5_breakout,
            "conclusion": "当前无明确可交易主线，建议观望。",
            "decision": {
                "what_to_buy": "无",
                "when_to_buy": "等待资金回流",
                "when_not_to_buy": "全面回避",
            },
            "all_theme_ranking": [
                {"theme": cat, "mainline_score": m["mainline_score"],
                 "capital_continuity": m["capital_continuity"],
                 "capacity_score": m["capacity_score"],
                 "diffusion_score": m["diffusion_score"],
                 "leader_structure": m["leader_structure"],
                 "short_term_score": m.get("short_term_score", 0),
                 "mid_term_score": m.get("mid_term_score", 0),
                 "acceleration_signal": m.get("acceleration_signal", 0),
                 "market_share_pct": m["market_share_pct"],
                 "n_active_subs": m.get("n_active_subs", 0),
                 "n_total_subs": m.get("n_total_subs", 0),
                 "avg_trend": m.get("avg_trend", 0),
                 "avg_change5_pct": m.get("avg_change5_pct", 0),
                 "avg_change10_pct": m.get("avg_change10_pct", 0),
                 "avg_change20_pct": m.get("avg_change20_pct", 0),
                 "avg_change60_pct": m.get("avg_change60_pct", 0),
                 "above_ma20_ratio": m.get("above_ma20_ratio", 0),
                 "above_ma60_ratio": m.get("above_ma60_ratio", 0),
                 "avg_ma20_slope_pct": m.get("avg_ma20_slope_pct", 0),
                 "avg_ma60_slope_pct": m.get("avg_ma60_slope_pct", 0)}
                for cat, m in sorted_cats
            ],
        }
        save_path = os.path.join(CACHE_DIR, f"trend_lifecycle_v11_{trade_date}.json")
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        print(f"\n[已保存] {save_path}")
        return

    # 获取主线主题的详细指标
    primary_m = cat_metrics[primary]
    lifecycle = determine_lifecycle_v11(primary_m)

    # ========== 3. 成份股筛选 ==========
    valid_stocks = filter_valid_constituents(primary, all_themes, primary_map)
    valid_count = len(valid_stocks)
    total_raw = sum(
        1 for t in all_themes if t.get("top_category") == primary
        for s in t.get("stocks", [])
        if primary_map.get(s.get("ts_code", "")) == primary
    )

    # ========== 4. 唯一真龙识别 ==========
    leader = identify_leader(valid_stocks, primary, cat_metrics)

    # ========== 5. 龙头分层 ==========
    classified = classify_leaders(valid_stocks, leader, primary)

    # ========== 6. 买点分析（为每只分层股票计算）==========
    mainline_stocks_with_entry = []

    # 真龙头
    if classified["true_leader"]:
        s = classified["true_leader"]
        s_info = dict(s)
        s_info["role_str"] = "真龙头"
        ea = compute_entry_analysis(s_info, primary, lifecycle, True)
        mainline_stocks_with_entry.append({
            **s,
            "role": "真龙头",
            "entry_analysis": ea,
        })

    # 中军
    for s in classified["trend_core_companies"]:
        s_info = dict(s)
        s_info["role_str"] = "趋势中军"
        ea = compute_entry_analysis(s_info, primary, lifecycle, True)
        mainline_stocks_with_entry.append({
            **s,
            "role": "趋势中军",
            "entry_analysis": ea,
        })

    # 补涨龙
    for s in classified["followup_leaders"]:
        s_info = dict(s)
        s_info["role_str"] = "补涨龙"
        ea = compute_entry_analysis(s_info, primary, lifecycle, True)
        mainline_stocks_with_entry.append({
            **s,
            "role": "补涨龙",
            "entry_analysis": ea,
        })

    # 卡位龙
    if classified["rotation_leader"]:
        s = classified["rotation_leader"]
        s_info = dict(s)
        s_info["role_str"] = "卡位龙"
        ea = compute_entry_analysis(s_info, primary, lifecycle, True)
        mainline_stocks_with_entry.append({
            **s,
            "role": "卡位龙",
            "entry_analysis": ea,
        })

    # ========== 7. 风险边界 ==========
    # 依据生命周期 + 资金流入力度判定风险
    if lifecycle == "退潮期":
        risk_boundary = {
            "stop_loss_pct": 0,
            "max_position_pct": 0,
            "avoid": True,
            "reason": "主题进入退潮期，资金持续流出，全面回避",
        }
    elif lifecycle == "启动期":
        risk_boundary = {
            "stop_loss_pct": 5,
            "max_position_pct": 15,
            "avoid": False,
            "reason": "资金初步进入，尚未形成扩散，轻仓试错为主",
        }
    elif lifecycle == "主升期":
        risk_boundary = {
            "stop_loss_pct": 8,
            "max_position_pct": 35,
            "avoid": False,
            "reason": "主升期，趋势结构稳定，可分批加仓龙头和中军",
        }
    elif lifecycle == "高潮期":
        risk_boundary = {
            "stop_loss_pct": 5,
            "max_position_pct": 20,
            "avoid": False,
            "reason": "高潮期，情绪高涨但需警惕分歧，降低仓位保护利润",
        }
    else:  # 分歧期
        risk_boundary = {
            "stop_loss_pct": 5,
            "max_position_pct": 15,
            "avoid": False,
            "reason": "分歧期，资金分流，等待明确方向确认",
        }

    # ========== 8. 决策三件套 ==========
    # 排序：按 entry_score 找最优交易标的
    tradable_sorted = sorted(
        [x for x in mainline_stocks_with_entry if x["entry_analysis"]["position_action"] != "回避"],
        key=lambda x: -x["entry_analysis"]["entry_score"]
    )

    if tradable_sorted:
        top_buy = tradable_sorted[0]
        what_to_buy = f"{top_buy['role']} {top_buy['name']}({top_buy['ts_code']}) - {top_buy['entry_analysis']['entry_type']}型买点"
        when_to_buy = f"{top_buy['entry_analysis']['position_action']}，entry_score {top_buy['entry_analysis']['entry_score']}，风险 {top_buy['entry_analysis']['risk_level']}"
    else:
        what_to_buy = "当前无符合条件的可交易标的"
        when_to_buy = "观望等待"

    # 不该买的条件
    if lifecycle in ("退潮期",):
        when_not_to_buy = f"{primary}处于退潮期，资金持续流出，全面回避"
    elif primary_m["above_ma5_ratio"] < 30:
        when_not_to_buy = f"{primary}站稳MA5比例仅{primary_m['above_ma5_ratio']:.1f}%，结构偏弱，避免追高"
    else:
        when_not_to_buy = f"避免买{primary}内成交额<5亿的小票，避免纯概念无产业匹配标的，避免高位追涨"

    conclusion = f"当前唯一可交易主线为【{primary}】，处于【{lifecycle}】，主线评分{primary_m['mainline_score']}，市场占比{primary_m['market_share_pct']:.1f}%。"

    # ========== 8.5 爆发潜力分（新增模块）==========
    # 计算所有一级主题的爆发潜力分，识别"资金悄悄进场但还没被市场发现"的主题
    breakout_results = compute_breakout_score(all_themes, categories, primary_map, cat_metrics)
    top5_breakout = breakout_results[:5]

    # ========== 9. 组装最终输出 ==========
    output = {
        "trade_date": trade_date,
        "engine": "V11",
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),

        "primary_mainline": primary,
        "lifecycle_stage": lifecycle,
        "market_total_amount_yi": round(market_total, 1),

        "mainline_metrics": primary_m,
        "other_themes": other_classifications,

        "breakout_themes_top5": top5_breakout,

        "constituent_filter": {
            "raw_count_total": total_raw,
            "valid_count": valid_count,
            "filter_rate": round((total_raw - valid_count) / total_raw * 100, 1) if total_raw > 0 else 0,
            "filter_rules": [
                "剔除：成交额<5亿",
                "剔除：无行业归属(industry_match==False)",
                "剔除：无产业链位置(trend_score<30 AND combined_score<30)",
                "剔除：疑似单日脉冲(change_5d>15%但ma10_slope<0且未站稳MA5)",
            ],
        },

        "leaders": mainline_stocks_with_entry,

        "tradable_targets": [
            {
                "name": x["name"],
                "ts_code": x["ts_code"],
                "role": x["role"],
                "amount_yi": x["amount_yi"],
                "trend_score": x["trend_score"],
                "combined_score": x["combined_score"],
                "sub_theme": x.get("sub_theme", ""),
                "entry_analysis": x["entry_analysis"],
            }
            for x in tradable_sorted
        ],

        "risk_boundary": risk_boundary,

        "decision": {
            "what_to_buy": what_to_buy,
            "when_to_buy": when_to_buy,
            "when_not_to_buy": when_not_to_buy,
        },

        "conclusion": conclusion,

        # 其他主题排名（简要）
        "all_theme_ranking": [
            {"theme": cat, "mainline_score": m["mainline_score"],
             "lifecycle_stage": determine_lifecycle_v11(m),
             "capital_continuity": m["capital_continuity"],
             "capacity_score": m["capacity_score"],
             "diffusion_score": m["diffusion_score"],
             "leader_structure": m["leader_structure"],
             "short_term_score": m.get("short_term_score", 0),
             "mid_term_score": m.get("mid_term_score", 0),
             "acceleration_signal": m.get("acceleration_signal", 0),
             "market_share_pct": m["market_share_pct"],
             "n_active_subs": m.get("n_active_subs", 0),
             "n_total_subs": m.get("n_total_subs", 0),
             "avg_change5_pct": m.get("avg_change5_pct", 0),
             "avg_change10_pct": m.get("avg_change10_pct", 0),
             "avg_change20_pct": m.get("avg_change20_pct", 0),
             "avg_change60_pct": m.get("avg_change60_pct", 0),
             "above_ma20_ratio": m.get("above_ma20_ratio", 0),
             "above_ma60_ratio": m.get("above_ma60_ratio", 0),
             "avg_ma20_slope_pct": m.get("avg_ma20_slope_pct", 0),
             "avg_ma60_slope_pct": m.get("avg_ma60_slope_pct", 0),
             "avg_trend": m.get("avg_trend", 0),
             "classification": next(
                 (o["type"] for o in other_classifications if o["theme"] == cat),
                 "主线" if cat == primary else "其他"
             )}
            for cat, m in sorted_cats
        ],
    }

    save_path = os.path.join(CACHE_DIR, f"trend_lifecycle_v11_{trade_date}.json")
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"\n[已保存] {save_path}")


if __name__ == "__main__":
    main()
