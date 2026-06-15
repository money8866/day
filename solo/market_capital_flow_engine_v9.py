#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Market Structure & Capital Flow Engine V9
A股市场"主线识别 + 资金路径 + 生命周期交易决策引擎

FinalScore = 0.55 × TrendMoneyScore
          + 0.30 × StructureScore
          + 0.15 × PhaseBonus

核心：资金持续性 > 单日强度 > 涨停数量
"""
import json
import os
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")


def load_all_data():
    """加载所需数据"""
    data_files = {
        "constituents": os.path.join(CACHE_DIR, "theme3_constituents_20260612.json"),
        "v61": os.path.join(CACHE_DIR, "theme_ranking_v6_1_20260612.json"),
        "v71": os.path.join(CACHE_DIR, "trend_lifecycle_v7_1_20260612.json"),
        "capital_flow": os.path.join(CACHE_DIR, "theme_capital_flow_20260612.json"),
        "lagging": os.path.join(CACHE_DIR, "theme_lagging_path_20260612.json"),
        "breakout": os.path.join(CACHE_DIR, "theme_breakout_selector_20260612.json"),
    }
    data = {}
    for key, path in data_files.items():
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data[key] = json.load(f)
                print(f"✓ {key}: {os.path.basename(path)}")
            except Exception as e:
                data[key] = None
        else:
            data[key] = None
    return data


def compute_trend_money_score(category, themes_in_cat, stocks_by_sub, v61_theme, market_total):
    """
    TrendMoneyScore (0-100) 权重55% - 钱是否持续在该主题里

    子因子：
    1. 成交额占比（核心）
    2. 资金连续流入天数代理（由 change_10d_pct + combined_score 代理
    3. 主力净流入趋势（trend_score + ma10_slope
    4. 回撤后资金回补（close_above_ma5占比）
    5. 跨子主题持续流入（扩散广度）

    评分规则：
    100: 资金持续多日净流入 + 占比市场核心
    80: 资金稳定流入
    60: 间歇流入
    40: 短期流入
    20: 流出
    """
    # 1. 成交额占比
    cat_amount = sum(
        sum((s.get("avg_amount_5d") or 0) for s in t.get("stocks", []))
        for t in themes_in_cat
    ) / 1e8
    amount_share = cat_amount / market_total * 100 if market_total > 0 else 0

    # 2. 资金连续性代理：10日涨幅中值
    all_stocks = []
    for t in themes_in_cat:
        all_stocks.extend(t.get("stocks", []))
    if not all_stocks:
        return 20, {"note": "无股票"}
    n = len(all_stocks)

    avg_5d = sum((s.get("change_5d_pct") or 0) for s in all_stocks) / n
    avg_10d = sum((s.get("change_10d_pct") or 0) for s in all_stocks) / n
    avg_trend = sum((s.get("trend_score") or 0) for s in all_stocks) / n
    avg_combined = sum((s.get("combined_score") or 0) for s in all_stocks) / n
    avg_ma10_slope = sum((s.get("ma10_slope_pct") or 0) for s in all_stocks) / n
    n_above_ma5 = sum(1 for s in all_stocks if s.get("close_above_ma5") is True)
    ratio_above_ma5 = n_above_ma5 / n * 100

    # 3. 主力净流入趋势（趋势分>60的占比
    n_strong_trend = sum(1 for s in all_stocks if (s.get("trend_score") or 0) >= 60)
    strong_trend_ratio = n_strong_trend / n * 100

    # 4. 回撤后资金回补：站稳MA5 + MA10向上
    recover_score = ratio_above_ma5 * 0.5 + avg_ma10_slope * 10

    # 5. 跨子主题持续流入：有多少子主题资金>0
    sub_with_inflow = 0
    for t in themes_in_cat:
        sub_stocks = t.get("stocks", [])
        if sub_stocks:
            sub_avg = sum((s.get("change_5d_pct") or 0) for s in sub_stocks) / len(sub_stocks)
            if sub_avg > 0:
                sub_with_inflow += 1
    cross_sub_ratio = sub_with_inflow / max(len(themes_in_cat), 1) * 100

    # 综合评分（按权重
    # 成交额占比：市场核心（占比5%+
    amount_core_score = min(100, amount_share * 10)
    continuity_score = min(100, max(0, avg_10d * 8 + 50))
    inflow_trend_score = min(100, avg_trend * 1)
    recover_score_val = min(100, max(0, recover_score))
    cross_sub_score = min(100, cross_sub_ratio * 1)

    score = (
        amount_core_score * 0.35
        + continuity_score * 0.25
        + inflow_trend_score * 0.15
        + recover_score_val * 0.15
        + cross_sub_score * 0.10
    )

    detail = {
        "amount_yi": round(cat_amount, 1),
        "market_share_pct": round(amount_share, 2),
        "avg_change_5d": round(avg_5d, 2),
        "avg_change_10d": round(avg_10d, 2),
        "avg_trend_score": round(avg_trend, 1),
        "avg_ma10_slope": round(avg_ma10_slope, 2),
        "ma5_coverage": round(ratio_above_ma5, 1),
        "strong_trend_ratio": round(strong_trend_ratio, 1),
        "sub_with_inflow": sub_with_inflow,
        "total_sub_themes": len(themes_in_cat),
    }
    return round(min(100, max(0, score)), 1), detail


def compute_structure_score(category, themes_in_cat, v61_theme, v71_theme, stocks_by_sub):
    """
    StructureScore (0-100) 权重30% - 这个主题能不能走趋势

    子因子：
    1. 龙头是否持续切换（多只龙头而非单龙）
    2. 是否存在子主题扩散链路
    3. 是否形成产业链扩展（A→B→C）
    4. 是否出现平台整理后再突破
    5. 是否有跨板块共振

    评分规则：
    100: 多轮龙头 + 子链扩散完整 + 趋势结构稳定
    80: 结构完整趋势
    60: 局部结构
    40: 单点行情
    20: 无结构
    """
    all_stocks = []
    for t in themes_in_cat:
        all_stocks.extend(t.get("stocks", []))
    n = len(all_stocks)
    if n == 0:
        return 20, {}

    # 1. 龙头切换：龙头股是否多轮
    leaders = [s for s in all_stocks if s.get("role") in ("龙头",) or (s.get("limit_up_days") or 0) >= 1]
    leaders.sort(key=lambda s: -(s.get("avg_amount_5d") or 0), reverse=True)
    top_leaders = leaders[:5]
    leader_amt = min(100, len(top_leaders) * 30 + (100 if len(top_leaders) else 0)

    # 2. 子主题扩散链路：有多少子主题
    n_subs = len(themes_in_cat)
    subs_with_amount = sum(
        1 for t in themes_in_cat if t.get("stocks", [])
        and sum((s.get("avg_amount_5d") or 0) for s in t.get("stocks", [])) / 1e8 > 5
    )
    sub_link_score = min(100, subs_with_amount * 25 + n_subs * 10)

    # 3. 产业链扩展（A→B→C）— 有多少个不同子主题且每个子主题都有龙头和中军
    # 使用 combined_score 来判断产业链完整性
    leader_and_mid = sum(1 for t in themes_in_cat 
                   if any(s.get("role") in ("龙头", "中军") for s in t.get("stocks", []))
    chain_score = min(100, leader_and_mid * 20 + n_subs * 5)

    # 4. 平台整理后再突破（MA10斜率 +站稳MA5
    avg_ma10_slope = sum((s.get("ma10_slope_pct") or 0) for s in all_stocks) / n
    n_above_ma5 = sum(1 for s in all_stocks if s.get("close_above_ma5") is True)
    consolidation_score = min(100, n_above_ma5 / n * 100 + avg_ma10_slope * 10)

    # 5. 跨板块共振 -从v6.1 + v7.1 扩散分
    exp_score = v61_theme.get("expansion_score", 50) if v61_theme else 50
    long_cycle = v71_theme.get("long_cycle_score", 50) if v71_theme else 50
    resonance_score = min(100, (exp_score + long_cycle) / 2)

    score = (
        leader_amt * 0.20
        + sub_link_score * 0.25
        + chain_score * 0.20
        + consolidation_score * 0.