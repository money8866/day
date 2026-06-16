#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Purity Engine V4.1
========================
判断一只股票是不是某主题的"真正受益者"

核心 V4.1 FinalScore (0-100):
  0.30 × ThemeStrength (主题强度)
+ 0.25 × BreakoutScore (个股补涨爆发潜力)
+ 0.20 × ThemePurity (主题纯度 — 5因子加权)
+ 0.15 × MarketRecognition (市场认可度)
+ 0.10 × CapitalFlow (资金流量)

纯度等级：核心(80+) / 高纯度(70-80) / 可交易(60-70) / 弱关联(50-60) / 伪概念(<50)
补涨过滤：纯度 >= 60 才能进入可交易池
"""
import json
import os
import glob
import numpy as np
import pandas as pd
from datetime import datetime
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")


def safe_avg(values, default=0.0):
    """安全计算平均值，避免空列表和None"""
    vs = [float(v) for v in values if v is not None and v != ""]
    return float(np.mean(vs)) if vs else default

# =====================================================================
# 数据加载层
# =====================================================================

def load_theme_config():
    """加载 theme3.json 主题配置"""
    with open(os.path.join(BASE_DIR, "theme3.json"), "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("THEME_FLAT_MAP", {})


def load_constituents(date_str=None):
    """加载主题成分股数据"""
    if date_str:
        path = os.path.join(CACHE_DIR, f"theme3_constituents_{date_str}.json")
    else:
        pattern = os.path.join(CACHE_DIR, "theme3_constituents_*.json")
        files = sorted(glob.glob(pattern))
        path = files[-1] if files else None

    if not path or not os.path.exists(path):
        return None, None

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data, os.path.basename(path)


def load_east_money_boards():
    """加载东财板块数据，建立股票->板块映射"""
    csv_path = os.path.join(CACHE_DIR, "dc_all_members.csv")
    if not os.path.exists(csv_path):
        return defaultdict(list)

    df = pd.read_csv(csv_path)
    stock_boards = defaultdict(list)
    for _, row in df.iterrows():
        code = row.get("con_code", "")
        name = row.get("name", "")
        board = row.get("concept_name", "")
        if code and board:
            stock_boards[code].append(board)
            stock_boards[name].append(board)

    return stock_boards


# =====================================================================
# 核心评分逻辑：5 维纯度因子
# =====================================================================

def calc_business_purity(stock, theme_cfg, stock_boards):
    """
    主营业务纯度 (0-100) - 最高权重 0.40
    判断：公司主要收入来源是否核心主题业务
    """
    name = stock.get("name", "")
    ts_code = stock.get("ts_code", "")

    # 基础分
    score = 30  # 默认给一个基础分，允许被扣除

    # 1. 主题关键词在名称/描述中的匹配
    business_dna_tags = theme_cfg.get("business_dna_tags", []) or []
    core_semantic = theme_cfg.get("core_semantic", []) or []
    weak_tags = theme_cfg.get("weak_positive_tags", []) or []
    negative_tags = list((theme_cfg.get("negative_pressure_tags", {}) or {}).keys())

    # 提取关键业务关键词
    strong_keywords = [
        kw for kw in business_dna_tags
        if len(kw) >= 2 and kw not in ("", None)
    ]
    # 也加入 core_semantic 中的关键词
    for sem in core_semantic:
        if isinstance(sem, str):
            # 提取2字以上有意义的词
            parts = sem.split("/")
            for p in parts:
                if 2 <= len(p) <= 8:
                    strong_keywords.append(p)

    # 名称强匹配
    name_match_count = 0
    for kw in strong_keywords:
        if kw in name:
            name_match_count += 1

    # 2. 东财板块匹配
    boards = stock_boards.get(ts_code, []) + stock_boards.get(name, [])
    # 去重
    boards = list(set(boards))

    board_match_count = 0
    # 核心匹配（business_dna_tags 直接出现在板块名）
    for board in boards:
        if not board:
            continue
        for kw in strong_keywords:
            if kw in board:
                board_match_count += 1
                break

    # 弱匹配（weak_positive_tags）
    weak_board_match = 0
    for board in boards:
        if not board:
            continue
        for kw in weak_tags:
            if kw in board:
                weak_board_match += 1
                break

    # 负向匹配（排除行业）
    negative_match = 0
    for board in boards:
        if not board:
            continue
        for kw in negative_tags:
            if kw in board:
                negative_match += 1
                break

    # 3. 成分股中的原始 score（来自 theme3_constituents）
    orig_score = stock.get("score", 0) or 0

    # 4. 综合评估
    if name_match_count >= 1 and board_match_count >= 3:
        score = 95  # 名称+板块强匹配
    elif name_match_count >= 1 and board_match_count >= 2:
        score = 85
    elif board_match_count >= 4:
        score = 85
    elif name_match_count >= 1 and board_match_count >= 1:
        score = 75
    elif board_match_count >= 2:
        score = 65
    elif board_match_count >= 1 or (name_match_count >= 1 and weak_board_match >= 1):
        score = 55
    elif weak_board_match >= 2 or orig_score >= 30:
        score = 45
    elif weak_board_match >= 1 or orig_score >= 20:
        score = 35
    elif orig_score >= 10:
        score = 25
    else:
        score = 15  # 仅概念沾边

    # 5. industry_roles 强匹配（如果股票名称或板块匹配了特定产业链角色）
    industry_roles = theme_cfg.get("industry_roles", {}) or {}
    for role_name, weight in industry_roles.items():
        if not role_name or len(role_name) < 2:
            continue
        key = role_name[:3] if len(role_name) >= 3 else role_name
        board_match = any(key in board for board in boards if board)
        name_match = key in name
        if board_match or name_match or (stock.get("role") == "龙头" and "龙头" in role_name):
            if weight >= 0.25:
                score += 10
                break

    # 6. 负向惩罚
    if negative_match >= 2:
        score -= 20
    elif negative_match >= 1:
        score -= 10

    # 7. 结合原始匹配 score
    score = score * 0.7 + min(100, orig_score * 2.5) * 0.3

    # 8. 行业匹配字段
    if stock.get("industry_match"):
        score += 5

    return min(100, max(0, round(score, 1)))


def calc_industry_chain(stock, theme_cfg):
    """
    产业链节点重要度 (0-100) - 权重 0.25
    判断：公司位于主题产业链什么位置
    """
    role = stock.get("role", "")
    chain_distance = stock.get("chain_distance", 0) or 0
    combined_score = stock.get("combined_score", 0) or 0

    # 基于 role 评估
    role_score = {
        "龙头": 95,
        "中军": 80,
        "补涨": 55,
        "观察": 40,
    }.get(role, 40)

    # 链距离惩罚
    distance_penalty = chain_distance * 5

    # combined_score 反映综合匹配度
    combined_component = min(100, combined_score)

    # 行业角色权重检查
    industry_roles = theme_cfg.get("industry_roles", {}) or {}
    stock_roles = theme_cfg.get("stock_role_mapping", {}) or {}

    # 如果该主题有明确的龙头/中军定义，加分
    role_map_score = 50
    if role == "龙头" and "龙头" in stock_roles:
        role_map_score = 90
    elif role == "中军" and "中军" in stock_roles:
        role_map_score = 80
    elif role == "补涨" and "补涨" in stock_roles:
        role_map_score = 60

    final_score = (
        role_score * 0.40
        + combined_component * 0.35
        + role_map_score * 0.20
        + max(0, 100 - distance_penalty) * 0.05
    )

    return min(100, max(0, round(final_score, 1)))


def calc_revenue_contribution(stock, theme_cfg, stock_boards):
    """
    收入贡献度 (0-100) - 权重 0.15
    判断：主题业务收入占比
    """
    # 由于我们没有直接的财务收入数据，用以下信号代理：
    # 1. 市值规模（更大市值 = 业务更成熟 = 收入贡献可能更高）
    total_mv_wan = stock.get("total_mv_wan", 0) or 0
    total_mv_yi = total_mv_wan / 10000

    # 2. 成交量（资金关注度 = 市场认为其是该主题的核心受益者）
    avg_amount_5d = stock.get("avg_amount_5d", 0) or 0
    avg_amount_yi = avg_amount_5d / 1e8

    # 3. role（龙头 = 核心收入）
    role = stock.get("role", "")

    # 4. 板块匹配数量（更多主题相关板块 = 业务更集中）
    name = stock.get("name", "")
    ts_code = stock.get("ts_code", "")
    boards = list(set(stock_boards.get(ts_code, []) + stock_boards.get(name, [])))
    business_dna_tags = theme_cfg.get("business_dna_tags", []) or []

    theme_board_count = 0
    for board in boards:
        if not board:
            continue
        for kw in business_dna_tags:
            if kw in board:
                theme_board_count += 1
                break

    # 5. 综合评分
    size_score = 0
    if total_mv_yi >= 500:
        size_score = 90
    elif total_mv_yi >= 200:
        size_score = 75
    elif total_mv_yi >= 50:
        size_score = 60
    elif total_mv_yi >= 10:
        size_score = 50
    else:
        size_score = 40

    amount_score = 0
    if avg_amount_yi >= 20:
        amount_score = 95
    elif avg_amount_yi >= 10:
        amount_score = 85
    elif avg_amount_yi >= 5:
        amount_score = 75
    elif avg_amount_yi >= 2:
        amount_score = 65
    elif avg_amount_yi >= 0.5:
        amount_score = 55
    else:
        amount_score = 40

    role_score = {"龙头": 90, "中军": 80, "补涨": 55, "观察": 40}.get(role, 40)

    board_count_score = 0
    if theme_board_count >= 4:
        board_count_score = 85
    elif theme_board_count >= 3:
        board_count_score = 70
    elif theme_board_count >= 2:
        board_count_score = 55
    elif theme_board_count >= 1:
        board_count_score = 40
    else:
        board_count_score = 25

    final_score = (
        size_score * 0.15
        + amount_score * 0.35
        + role_score * 0.30
        + board_count_score * 0.20
    )

    return min(100, max(0, round(final_score, 1)))


def calc_market_identity(stock, theme_cfg, stock_boards):
    """
    市场认知度 (0-100) - 权重 0.10
    判断：市场是否长期将其视为该主题核心标的
    """
    role = stock.get("role", "")
    limit_up_days = stock.get("limit_up_days", 0) or 0
    recent_up_days = stock.get("recent_up_days", 0) or 0
    avg_amount_5d = stock.get("avg_amount_5d", 0) or 0
    avg_amount_yi = avg_amount_5d / 1e8

    # 1. Role-based 基础分
    role_base = {"龙头": 90, "中军": 80, "补涨": 60, "观察": 40}.get(role, 35)

    # 2. 资金关注度（高成交量 = 高市场认知）
    if avg_amount_yi >= 15:
        money_score = 90
    elif avg_amount_yi >= 8:
        money_score = 75
    elif avg_amount_yi >= 3:
        money_score = 60
    elif avg_amount_yi >= 1:
        money_score = 45
    else:
        money_score = 30

    # 3. 历史涨停活跃度
    if limit_up_days >= 3:
        activity_score = 90
    elif limit_up_days >= 2:
        activity_score = 80
    elif limit_up_days >= 1:
        activity_score = 70
    elif recent_up_days >= 3:
        activity_score = 55
    elif recent_up_days >= 1:
        activity_score = 45
    else:
        activity_score = 35

    final_score = role_base * 0.45 + money_score * 0.30 + activity_score * 0.25

    return min(100, max(0, round(final_score, 1)))


def calc_historical_consistency(stock, theme_cfg, stock_boards):
    """
    历史主题一致性 (0-100) - 权重 0.10
    判断：过去一年是否持续归属于该主题
    """
    name = stock.get("name", "")
    ts_code = stock.get("ts_code", "")
    industry_match = stock.get("industry_match", False)
    score = stock.get("score", 0) or 0
    role = stock.get("role", "")

    # 1. 行业一致性
    industry_constraints = theme_cfg.get("industry_soft_constraints", {}) or {}
    boards = list(set(stock_boards.get(ts_code, []) + stock_boards.get(name, [])))

    industry_match_count = 0
    for industry_name, weight in industry_constraints.items():
        for board in boards:
            if board and industry_name in board:
                industry_match_count += 1
                break

    if industry_match and industry_match_count > 0:
        consistency_score = 90
    elif industry_match:
        consistency_score = 75
    elif industry_match_count >= 2:
        consistency_score = 70
    elif industry_match_count >= 1:
        consistency_score = 55
    elif score >= 30:
        consistency_score = 50
    elif score >= 20:
        consistency_score = 40
    else:
        consistency_score = 30

    # 2. Role 稳定性
    if role in ("龙头", "中军"):
        consistency_score += 10

    # 3. 负向压力行业惩罚
    negative_tags = list((theme_cfg.get("negative_pressure_tags", {}) or {}).keys())
    for board in boards:
        if board and any(tag in board for tag in negative_tags):
            consistency_score -= 10
            break

    return min(100, max(0, round(consistency_score, 1)))


# =====================================================================
# 综合评分层
# =====================================================================

def compute_theme_purity(stock, theme_cfg, stock_boards, theme_name=""):
    """
    计算单只股票对单一主题的综合纯度评分
    """
    business_purity = calc_business_purity(stock, theme_cfg, stock_boards)
    industry_chain = calc_industry_chain(stock, theme_cfg)
    revenue_contribution = calc_revenue_contribution(stock, theme_cfg, stock_boards)
    market_identity = calc_market_identity(stock, theme_cfg, stock_boards)
    historical_consistency = calc_historical_consistency(stock, theme_cfg, stock_boards)

    # 综合评分
    theme_purity_score = round(
        business_purity * 0.40
        + industry_chain * 0.25
        + revenue_contribution * 0.15
        + market_identity * 0.10
        + historical_consistency * 0.10
        , 1
    )

    # 纯度等级（新阈值）
    if theme_purity_score >= 80:
        purity_level = "核心"
    elif theme_purity_score >= 70:
        purity_level = "高纯度"
    elif theme_purity_score >= 60:
        purity_level = "可交易"
    elif theme_purity_score >= 50:
        purity_level = "弱关联"
    else:
        purity_level = "伪概念"

    # 角色定位
    role = stock.get("role", "")
    if theme_purity_score >= 80 and role == "龙头":
        final_role = "龙头"
    elif theme_purity_score >= 70 and role == "中军":
        final_role = "中军"
    elif theme_purity_score >= 60:
        final_role = "补涨"
    else:
        final_role = "观察"

    # 决策
    if theme_purity_score >= 60:
        decision = "保留"
    elif theme_purity_score >= 50:
        decision = "降权"
    else:
        decision = "剔除"

    # 解释
    reasons = []
    if business_purity >= 80:
        reasons.append("主营业务强匹配")
    elif business_purity >= 60:
        reasons.append("业务部分相关")
    else:
        reasons.append("业务关联度低")

    if industry_chain >= 80:
        reasons.append("产业链核心节点")
    elif industry_chain >= 60:
        reasons.append("产业链重要节点")

    if market_identity >= 70:
        reasons.append("市场高关注度")

    if theme_purity_score < 50:
        reasons.append("伪概念风险高")

    return {
        "stock": stock.get("name", ""),
        "ts_code": stock.get("ts_code", ""),
        "theme": theme_name,
        "theme_purity_score": theme_purity_score,
        "purity_level": purity_level,
        "breakdown": {
            "business_purity": business_purity,
            "industry_chain": industry_chain,
            "revenue_contribution": revenue_contribution,
            "market_identity": market_identity,
            "historical_consistency": historical_consistency,
        },
        "role": final_role,
        "decision": decision,
        "reason": "；".join(reasons),
        "raw_role": stock.get("role", ""),
        "metrics": {
            "change_5d_pct": stock.get("change_5d_pct", 0),
            "limit_up_days": stock.get("limit_up_days", 0),
            "avg_amount_5d_yi": round((stock.get("avg_amount_5d", 0) or 0) / 1e8, 2),
            "total_mv_yi": round((stock.get("total_mv_wan", 0) or 0) / 10000, 2),
        },
    }


# =====================================================================
# 主题级分析
# =====================================================================

def analyze_theme_purity(theme_data, theme_cfg_map, stock_boards, top_n=30):
    """
    4.1 升级：对单个主题的成分股进行纯度分析，并计算 FinalScore
    FinalScore = 0.30 * ThemeStrength + 0.25 * BreakoutScore
              + 0.20 * ThemePurity + 0.15 * MarketRecognition
              + 0.10 * CapitalFlow
    """
    theme_name = theme_data.get("theme_name", "未知主题")
    top_category = theme_data.get("top_category", "未知")
    stocks = theme_data.get("stocks", [])

    cfg = theme_cfg_map.get(theme_name, {})

    # Step 1: 计算每只股票的 5 因子 + FinalScore
    results = []
    for stock in stocks:
        # 1. ThemePurity — 已有
        purity_result = compute_theme_purity(stock, cfg, stock_boards, theme_name)

        # 2. ThemeStrength — 主题级强度，先预留占位，下一步统一计算
        # 3. BreakoutScore — 个股补涨爆发潜力 (0-100)
        limit_up_days = stock.get("limit_up_days", 0) or 0
        recent_up = stock.get("recent_up_days", 0) or 0
        change_5d = stock.get("change_5d_pct", 0) or 0
        ma10_slope = stock.get("ma10_slope_pct", 0) or 0
        above_ma5 = stock.get("close_above_ma5", False)
        avg_amount_5d = stock.get("avg_amount_5d", 0) or 0
        avg_amount_yi = avg_amount_5d / 1e8

        breakout_score = 0
        if limit_up_days >= 2:
            breakout_score += 40
        elif limit_up_days == 1:
            breakout_score += 30
        if recent_up >= 2:
            breakout_score += 15
        elif recent_up >= 1:
            breakout_score += 10
        if above_ma5 and ma10_slope > 0:
            breakout_score += 15
        if change_5d > 5:
            breakout_score += min(25, change_5d * 2)
        if avg_amount_yi > 5:
            breakout_score += 10
        breakout_score = min(100, breakout_score)

        # 4. MarketRecognition — 市场认可度 (0-100)
        role = stock.get("role", "")
        total_mv_yi = (stock.get("total_mv_wan", 0) or 0) / 10000
        mr_score = 0
        if role == "龙头":
            mr_score += 40
        elif role == "中军":
            mr_score += 30
        elif role == "补涨":
            mr_score += 20
        else:
            mr_score += 10
        # 市值因子（50-500 亿最优）
        if 50 <= total_mv_yi <= 500:
            mr_score += 30
        elif 20 <= total_mv_yi < 50 or 500 < total_mv_yi <= 1000:
            mr_score += 20
        else:
            mr_score += 10
        # 成交因子
        if avg_amount_yi >= 10:
            mr_score += 30
        elif avg_amount_yi >= 5:
            mr_score += 25
        elif avg_amount_yi >= 2:
            mr_score += 15
        else:
            mr_score += 5
        market_recognition = min(100, mr_score)

        # 5. CapitalFlow — 资金流量 (0-100)
        cf_score = 0
        if limit_up_days >= 1:
            cf_score += 40
        elif recent_up >= 2:
            cf_score += 30
        elif recent_up >= 1:
            cf_score += 20
        if change_5d > 10:
            cf_score += 20
        elif change_5d > 5:
            cf_score += 10
        if ma10_slope > 5:
            cf_score += 20
        elif ma10_slope > 2:
            cf_score += 10
        if avg_amount_yi >= 10:
            cf_score += 20
        elif avg_amount_yi >= 5:
            cf_score += 10
        capital_flow = min(100, cf_score)

        # 汇总到 purity_result（ThemeStrength 等下主题级计算后再补充）
        purity_result.update({
            "_breakout_score": round(breakout_score, 1),
            "_market_recognition": round(market_recognition, 1),
            "_capital_flow": round(capital_flow, 1),
            "_theme_purity_raw": purity_result["theme_purity_score"],
        })
        results.append(purity_result)

    # Step 2: 计算主题级 ThemeStrength（基于主题内股票整体表现）
    if results:
        avg_change = safe_avg([(r["metrics"]["change_5d_pct"] or 0) for r in results])
        total_limit_up = sum((r["metrics"]["limit_up_days"] or 0) >= 1 for r in results)
        avg_amount = safe_avg([r["metrics"]["avg_amount_5d_yi"] or 0 for r in results])

        theme_strength = (
            max(0, min(100, avg_change * 3)) * 0.35
            + min(100, total_limit_up * 8) * 0.35
            + min(100, avg_amount * 2) * 0.30
        )
        theme_strength = round(theme_strength, 1)
    else:
        theme_strength = 0.0

    # Step 3: 计算每只股票的 FinalScore（按 5 因子加权）
    for r in results:
        bp = r["_breakout_score"]
        mr = r["_market_recognition"]
        cf = r["_capital_flow"]
        tp = r["_theme_purity_raw"]
        ts = theme_strength

        final_score = round(
            ts * 0.30 + bp * 0.25 + tp * 0.20 + mr * 0.15 + cf * 0.10
            , 1
        )
        r["final_score"] = final_score
        r["theme_strength"] = ts
        r["breakout_score"] = bp
        r["theme_purity"] = tp
        r["market_recognition"] = mr
        r["capital_flow"] = cf
        # 清理临时字段
        for k in ["_breakout_score", "_market_recognition", "_capital_flow", "_theme_purity_raw"]:
            r.pop(k, None)

    # Step 4: 先按 purity_level 分层，各层内按 FinalScore 降序
    # 先过滤：保留纯度 >= 50 的（弱关联以上）
    filtered_results = [r for r in results if r["theme_purity_score"] >= 50]

    tiers = {
        "核心": [r for r in filtered_results if r["purity_level"] == "核心"],
        "高纯度": [r for r in filtered_results if r["purity_level"] == "高纯度"],
        "可交易": [r for r in filtered_results if r["purity_level"] == "可交易"],
        "弱关联": [r for r in filtered_results if r["purity_level"] == "弱关联"],
        "伪概念": [r for r in results if r["purity_level"] == "伪概念"],
    }

    # 每个池子内部按 FinalScore 降序
    for k in tiers:
        tiers[k].sort(key=lambda x: -x["final_score"])

    # 可交易池（>=60）
    tradable = []
    for level in ["核心", "高纯度", "可交易"]:
        tradable.extend(tiers[level])

    # 按 FinalScore 排序
    results_sorted = sorted(results, key=lambda x: -x["final_score"])
    avg_purity = sum(r["theme_purity_score"] for r in results) / len(results) if results else 0
    avg_final = sum(r["final_score"] for r in results) / len(results) if results else 0

    return {
        "theme_name": theme_name,
        "top_category": top_category,
        "total_stocks": len(stocks),
        "avg_purity_score": round(avg_purity, 1),
        "avg_final_score": round(avg_final, 1),
        "theme_strength": theme_strength,
        "tier_counts": {k: len(v) for k, v in tiers.items()},
        "core_pool": tiers["核心"][:top_n],
        "high_purity_pool": tiers["高纯度"][:top_n],
        "tradable_pool": tiers["可交易"][:top_n],
        "weak_pool": tiers["弱关联"][:top_n],
        "pseudo_pool": tiers["伪概念"][:top_n],
        "all_ranked": results_sorted[:top_n * 2],
        "recommended_tradable": tradable[:top_n],
    }


# =====================================================================
# 多主题批量分析 + 交叉验证
# =====================================================================

def analyze_multi_theme_purity(data, theme_cfg_map, stock_boards, target_themes=None, top_n=30):
    """
    批量分析多个主题，并进行交叉检测：
    - 哪些股票出现在多个主题中
    - 各主题平均纯度
    - 哪些主题纯度低（疑似混入伪概念）
    """
    all_themes = data.get("themes", [])
    if target_themes:
        all_themes = [t for t in all_themes if t.get("theme_name") in target_themes]

    theme_results = []
    stock_theme_mapping = defaultdict(list)

    for theme_data in all_themes:
        theme_name = theme_data.get("theme_name", "")
        result = analyze_theme_purity(theme_data, theme_cfg_map, stock_boards, top_n)
        theme_results.append(result)

        # 记录每只股票的主题归属
        for r in result["all_ranked"]:
            stock_theme_mapping[r["ts_code"]].append({
                "theme": theme_name,
                "score": r["theme_purity_score"],
                "purity_level": r["purity_level"],
                "role": r["role"],
            })

    # 4.1 升级：按 theme_strength * 0.5 + avg_final_score * 0.5 综合排序主题
    theme_results.sort(key=lambda x: -(
        x.get("theme_strength", 0) * 0.5
        + x.get("avg_final_score", 0) * 0.5
    ))

    # 多主题归属检测（同一股票出现在>=2个主题的伪概念/弱关联层）
    cross_issues = []
    for ts_code, mappings in stock_theme_mapping.items():
        if len(mappings) >= 2:
            low_purity = [m for m in mappings if m["score"] < 60]
            if low_purity:
                cross_issues.append({
                    "ts_code": ts_code,
                    "theme_count": len(mappings),
                    "low_purity_themes": [(m["theme"], round(m["score"], 1)) for m in low_purity],
                    "all_mappings": [(m["theme"], round(m["score"], 1), m["purity_level"]) for m in mappings],
                })

    return {
        "theme_purity_rankings": theme_results,
        "cross_theme_issues": sorted(cross_issues, key=lambda x: -x["theme_count"])[:50],
        "stock_theme_mapping_summary": dict(stock_theme_mapping),
    }


# =====================================================================
# 一级主题综合分计算
# =====================================================================

def compute_macro_theme_scores(theme_results):
    """
    将二级主题按一级主题聚合，计算一级主题综合分

    一级主题综合分 = 0.30 * Σ(子主题ThemeStrength) / N * 强度因子
                  + 0.25 * 高纯度+核心股票数 / 总股票数 * 质量因子
                  + 0.20 * Σ(子主题avg_final_score) / N * 得分因子
                  + 0.15 * Σ(子主题涨停数) * 涨停因子
                  + 0.10 * 主题数量得分（主题越聚焦越高）
    """
    from collections import defaultdict

    # 按一级主题聚合
    macro_map = defaultdict(list)
    for tr in theme_results:
        macro_map[tr["top_category"]].append(tr)

    macro_scores = {}
    for cat, sub_themes in macro_map.items():
        n = len(sub_themes)

        # 1. 平均主题强度（强度因子）
        avg_ts = safe_avg([t.get("theme_strength", 0) for t in sub_themes])
        # 强度因子：子主题数越少越聚焦，10个以内满分，>15个打7折
        focus_factor = min(1.0, 10 / n) if n > 0 else 0
        ts_component = avg_ts * focus_factor

        # 2. 质量因子（核心+高纯度股票占比）
        total_stocks = sum(t.get("total_stocks", 0) for t in sub_themes)
        core_high = (
            sum(t["tier_counts"].get("核心", 0) for t in sub_themes)
            + sum(t["tier_counts"].get("高纯度", 0) for t in sub_themes)
        )
        quality_factor = (core_high / total_stocks * 100) if total_stocks > 0 else 0

        # 3. 平均个股FinalScore（得分因子）
        avg_final = safe_avg([t.get("avg_final_score", 0) for t in sub_themes])

        # 4. 涨停总数（涨停因子）
        total_limit_up = sum(
            sum(1 for s in t.get("all_ranked", []) if (s.get("metrics", {}).get("limit_up_days") or 0) >= 1)
            for t in sub_themes
        )
        limit_factor = min(100, total_limit_up * 5)  # 每1个涨停=5分，上限100

        # 5. 主题数量得分（越聚焦越高）
        focus_score = max(0, 100 - (n - 1) * 5)  # 1个主题=100分，每多1个-5分

        # 综合分
        macro_final = round(
            ts_component * 0.30
            + quality_factor * 0.25
            + avg_final * 0.20
            + limit_factor * 0.15
            + focus_score * 0.10
            , 1
        )

        # 各子主题的 FinalScore 排序
        sub_ranked = []
        for t in sub_themes:
            sub_best = max(t.get("all_ranked", [])[:5] or [], key=lambda x: x.get("final_score", 0))
            sub_ranked.append({
                "theme_name": t["theme_name"],
                "theme_strength": t.get("theme_strength", 0),
                "avg_final_score": t.get("avg_final_score", 0),
                "best_stock": {
                    "stock": sub_best.get("stock", ""),
                    "ts_code": sub_best.get("ts_code", ""),
                    "final_score": sub_best.get("final_score", 0),
                    "purity_score": sub_best.get("theme_purity_score", 0),
                    "purity_level": sub_best.get("purity_level", ""),
                } if sub_best else None,
                "tier_counts": t["tier_counts"],
            })

        sub_ranked.sort(key=lambda x: -x["avg_final_score"])

        macro_scores[cat] = {
            "macro_theme": cat,
            "macro_final_score": macro_final,
            "n_sub_themes": n,
            "n_total_stocks": total_stocks,
            "ts_component": round(ts_component, 1),
            "quality_factor": round(quality_factor, 1),
            "avg_final_score": round(avg_final, 1),
            "limit_factor": round(limit_factor, 1),
            "focus_score": round(focus_score, 1),
            "core_high_stocks": core_high,
            "sub_themes_ranked": sub_ranked,
        }

    # 按一级主题综合分降序
    sorted_macros = sorted(macro_scores.values(), key=lambda x: -x["macro_final_score"])
    return sorted_macros


# =====================================================================
# 补涨资格验证（输出层）
# =====================================================================

def validate_breakout_candidates(trade_file_path, purity_results, min_purity=60):
    """
    从交易决策引擎的候选中，验证其补涨资格
    """
    if not os.path.exists(trade_file_path):
        return None

    with open(trade_file_path, "r", encoding="utf-8") as f:
        trade_data = json.load(f)

    # 建立股票->主题->纯度映射
    stock_purity = {}
    for theme_result in purity_results.get("theme_purity_rankings", []):
        for r in theme_result.get("all_ranked", []):
            key = r["ts_code"]
            if key not in stock_purity:
                stock_purity[key] = {}
            stock_purity[key][theme_result["theme_name"]] = r

    # 验证每个候选
    validated = []
    for candidate in trade_data.get("top_trade_candidates", []):
        stock_name = candidate.get("stock", "")
        # 提取代码
        code = stock_name
        if "(" in stock_name and ")" in stock_name:
            code = stock_name.split("(")[1].split(")")[0]

        purity_info = stock_purity.get(code, {})
        # 找到该股票在各主题中的最高纯度
        if purity_info:
            best_purity = max(purity_info.values(), key=lambda x: x["theme_purity_score"])
            eligible = best_purity["theme_purity_score"] >= min_purity
        else:
            best_purity = {"theme_purity_score": 0, "purity_level": "未评估", "role": "观察", "reason": "无主题数据"}
            eligible = False

        validated.append({
            "original_candidate": candidate,
            "purity_check": best_purity,
            "eligible_for_breakout": eligible,
            "recommendation": "保留（符合补涨资格）" if eligible else "剔除（纯度不足）",
        })

    return validated


# =====================================================================
# 输出与展示
# =====================================================================

def format_output(multi_result, trade_date, validated_candidates=None, macro_scores=None):
    """整理最终输出（V4.1 升级：展示 FinalScore、5因子拆解、一级主题综合分）"""
    output = {
        "trade_date": trade_date,
        "engine": "Theme Purity Engine V4.1",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "final_score_formula": "FinalScore = 0.30 * ThemeStrength + 0.25 * BreakoutScore + 0.20 * ThemePurity + 0.15 * MarketRecognition + 0.10 * CapitalFlow",
        "purity_threshold": {"core": 80, "high_purity": 70, "tradable": 60, "weak": 50, "pseudo": 0},
        "macro_theme_rankings": macro_scores or [],
        "summary": {
            "total_themes_analyzed": len(multi_result["theme_purity_rankings"]),
            "top_by_final_score": [
                {"theme_name": t["theme_name"], "category": t["top_category"],
                 "theme_strength": t.get("theme_strength", 0),
                 "avg_final_score": t.get("avg_final_score", 0),
                 "stocks": t["total_stocks"],
                 "core": t["tier_counts"].get("核心", 0),
                 "high": t["tier_counts"].get("高纯度", 0),
                 "tradable": t["tier_counts"].get("可交易", 0),
                 }
                for t in multi_result["theme_purity_rankings"][:10]
            ],
            "bottom_by_final_score": [
                {"theme_name": t["theme_name"], "category": t["top_category"],
                 "theme_strength": t.get("theme_strength", 0),
                 "avg_final_score": t.get("avg_final_score", 0),
                 "stocks": t["total_stocks"],
                 "pseudo_count": t["tier_counts"].get("伪概念", 0),
                 }
                for t in multi_result["theme_purity_rankings"][-10:]
            ],
        },
        "theme_details": [],
        "cross_issues": multi_result["cross_theme_issues"][:20],
        "trade_candidate_validation": validated_candidates or [],
    }

    for theme_result in multi_result["theme_purity_rankings"]:
        output["theme_details"].append({
            "theme_name": theme_result["theme_name"],
            "top_category": theme_result["top_category"],
            "theme_strength": theme_result.get("theme_strength", 0),
            "avg_final_score": theme_result.get("avg_final_score", 0),
            "avg_purity_score": theme_result["avg_purity_score"],
            "tier_counts": theme_result["tier_counts"],
            "core_pool": [
                {
                    "stock": r["stock"],
                    "ts_code": r["ts_code"],
                    "final_score": r.get("final_score", 0),
                    "purity_score": r["theme_purity_score"],
                    "purity_level": r["purity_level"],
                    "role": r["role"],
                    "decision": r["decision"],
                    "reason": r["reason"],
                    "theme_strength": r.get("theme_strength", 0),
                    "breakout_score": r.get("breakout_score", 0),
                    "theme_purity": r.get("theme_purity", 0),
                    "market_recognition": r.get("market_recognition", 0),
                    "capital_flow": r.get("capital_flow", 0),
                }
                for r in theme_result["core_pool"]
            ],
            "high_purity_pool": [
                {
                    "stock": r["stock"],
                    "ts_code": r["ts_code"],
                    "final_score": r.get("final_score", 0),
                    "purity_score": r["theme_purity_score"],
                    "purity_level": r["purity_level"],
                    "role": r["role"],
                    "decision": r["decision"],
                }
                for r in theme_result["high_purity_pool"]
            ],
            "tradable_pool": [
                {
                    "stock": r["stock"],
                    "ts_code": r["ts_code"],
                    "final_score": r.get("final_score", 0),
                    "purity_score": r["theme_purity_score"],
                    "purity_level": r["purity_level"],
                    "role": r["role"],
                    "decision": r["decision"],
                }
                for r in theme_result["tradable_pool"]
            ],
            "pseudo_pool": [
                {
                    "stock": r["stock"],
                    "ts_code": r["ts_code"],
                    "final_score": r.get("final_score", 0),
                    "purity_score": r["theme_purity_score"],
                    "purity_level": r["purity_level"],
                    "role": r["role"],
                    "decision": r["decision"],
                }
                for r in theme_result["pseudo_pool"]
            ],
        })

    return output


def print_human_report(output, validated_candidates=None):
    """打印人类可读的报告（V4.1 升级：展示 FinalScore、5因子、一级主题综合分）"""
    print("\n" + "=" * 100)
    print(f"  Theme Purity Engine V4.1   主题纯度+补涨分析报告   trade_date = {output['trade_date']}")
    print(f"  FinalScore = 0.30 * ThemeStrength + 0.25 * BreakoutScore + 0.20 * ThemePurity + 0.15 * MarketRecognition + 0.10 * CapitalFlow")
    print("=" * 100)

    # 0. 一级主题综合排名
    macro_rankings = output.get("macro_theme_rankings", [])
    if macro_rankings:
        print(f"\n【一级主题综合排名】")
        print(f"  {'排名':<4} {'主题':<10} {'综合分':>6} {'子主题':>4} {'总股票':>6} "
              f"{'强度分':>6} {'质量分':>6} {'涨停分':>6} {'聚焦分':>6}")
        print(f"  {'─'*4} {'─'*10} {'─'*6} {'─'*4} {'─'*6} {'─'*6} {'─'*6} {'─'*6} {'─'*6}")
        for i, m in enumerate(macro_rankings, 1):
            print(f"  {i:<4} {m['macro_theme']:<10} {m['macro_final_score']:>6.1f} "
                  f"{m['n_sub_themes']:>4} {m['n_total_stocks']:>6} "
                  f"{m['ts_component']:>6.1f} {m['quality_factor']:>6.1f} "
                  f"{m['limit_factor']:>6.1f} {m['focus_score']:>6.1f}")

        # 一级主题下 Top 3 子主题
        for m in macro_rankings[:4]:
            if m.get("sub_themes_ranked"):
                print(f"\n  ▶ {m['macro_theme']} 子主题详情（Top 3）：")
                for sub in m["sub_themes_ranked"][:3]:
                    bs = sub.get("best_stock") or {}
                    bs_name = f"{bs.get('stock','')}({bs.get('ts_code','')})" if bs else "无"
                    bs_info = f" Final={bs.get('final_score',0):.1f}" if bs else ""
                    print(f"    • {sub['theme_name']:<14} 强度={sub['theme_strength']:.1f} "
                          f"AvgFinal={sub['avg_final_score']:.1f}  "
                          f"最优={bs_name}{bs_info}")

    # 1. 主题排名（二级主题）
    print(f"\n{'─' * 100}")
    print(f"\n【二级主题排名 Top 10（按 FinalScore 综合排序）】")
    for t in output["summary"]["top_by_final_score"]:
        print(f"  {t['theme_name']:<14} 类别={t['category']:<8} "
              f"主题强度={t['theme_strength']:>5.1f} 平均Final={t['avg_final_score']:>5.1f} "
              f"股票数={t['stocks']:>4} 核心={t['core']:>3} 高纯度={t['high']:>3} 可交易={t['tradable']:>3}")

    print(f"\n【二级主题排名 Bottom 10】")
    for t in output["summary"]["bottom_by_final_score"]:
        print(f"  {t['theme_name']:<14} 类别={t['category']:<8} "
              f"主题强度={t['theme_strength']:>5.1f} 平均Final={t['avg_final_score']:>5.1f} "
              f"伪概念={t['pseudo_count']:>3}")

    # 2. 核心主题详细分析
    print(f"\n{'─' * 95}")
    print(f"  重点主题纯度分层（Top 3 主题 by FinalScore）")
    for theme in output["theme_details"][:3]:
        print(f"\n  ▶ {theme['theme_name']} ({theme['top_category']})")
        print(f"    主题强度={theme.get('theme_strength', 0):.1f}  平均Final={theme.get('avg_final_score', 0):.1f}  "
              f"核心 {theme['tier_counts']['核心']} | 高纯度 {theme['tier_counts']['高纯度']} | "
              f"可交易 {theme['tier_counts']['可交易']} | 弱关联 {theme['tier_counts']['弱关联']} | "
              f"伪概念 {theme['tier_counts']['伪概念']}")

        if theme.get("core_pool"):
            print(f"    核心池 (>=80):")
            for r in theme["core_pool"][:5]:
                print(f"      • {r['stock']:<10}({r['ts_code']}) "
                      f"Final={r['final_score']:>5.1f} [纯度{r['purity_score']:>5.1f} {r['role']}] → {r.get('reason','')}")
                print(f"         TS={r['theme_strength']:.0f}(30%) BO={r['breakout_score']:.0f}(25%) "
                      f"PU={r['theme_purity']:.0f}(20%) MR={r['market_recognition']:.0f}(15%) CF={r['capital_flow']:.0f}(10%)")

        if theme.get("high_purity_pool"):
            print(f"    高纯度池 (70-80):")
            for r in theme["high_purity_pool"][:5]:
                print(f"      • {r['stock']:<10}({r['ts_code']}) Final={r['final_score']:>5.1f} [纯度{r['purity_score']:>5.1f} {r['role']}]")

        if theme.get("tradable_pool"):
            print(f"    可交易池 (60-70):")
            for r in theme["tradable_pool"][:5]:
                print(f"      • {r['stock']:<10}({r['ts_code']}) Final={r['final_score']:>5.1f} [纯度{r['purity_score']:>5.1f} {r['role']}]")

        if theme.get("pseudo_pool"):
            print(f"    ⚠ 伪概念池 (<50):")
            for r in theme["pseudo_pool"][:3]:
                print(f"      • {r['stock']:<10}({r['ts_code']}) Final={r['final_score']:>5.1f} [纯度{r['purity_score']:>5.1f}] → 建议剔除")

    # 3. 跨主题问题
    if output.get("cross_issues"):
        print(f"\n{'─' * 100}")
        print(f"  跨主题重复归属检测（疑似概念沾边）")
        for issue in output["cross_issues"][:5]:
            print(f"    {issue['ts_code']} 归属 {issue['theme_count']} 个主题")
            for theme, score in issue["low_purity_themes"][:5]:
                print(f"      - {theme}: score={score}")

    # 4. 交易候选验证
    if validated_candidates:
        print(f"\n{'─' * 100}")
        print(f"  补涨交易候选验证（最低纯度门槛: >=60，按 FinalScore 排序）")
        for i, v in enumerate(validated_candidates, 1):
            c = v["original_candidate"]
            p = v["purity_check"]
            status = "✅ 保留" if v["eligible_for_breakout"] else "❌ 剔除"
            final = p.get("final_score", 0)
            purity = p.get("theme_purity_score", 0)
            print(f"  {i}. {c['stock']:<14} 原Score={c.get('score',0):>5.1f} "
                  f"纯度={purity:>5.1f} Final={final:>5.1f} [{p.get('purity_level','')}] → {status}")
            if not v["eligible_for_breakout"]:
                print(f"      原因: {p.get('reason', '无')}")

    print("\n" + "=" * 100)


# =====================================================================
# 主程序
# =====================================================================

def main():
    # 1. 加载数据
    print("📦 加载主题配置...")
    theme_cfg_map = load_theme_config()
    print(f"   ✓ 加载 {len(theme_cfg_map)} 个主题配置")

    print("📦 加载成分股数据...")
    data, filename = load_constituents()
    if not data:
        print("❌ 未找到成分股数据")
        return
    trade_date = data.get("trade_date", "unknown")
    print(f"   ✓ {filename} 共 {len(data.get('themes', []))} 个主题")

    print("📦 加载东财板块数据...")
    stock_boards = load_east_money_boards()
    print(f"   ✓ 板块数据就绪（共约 {len(stock_boards)} 个股票映射项）")

    # 2. 重点一级主题（按大类动态选择，自动包含所有子主题）
    target_categories = [
        "低空经济", "新能源", "半导体",
        "AI", "人形机器人", "商业航天",
    ]

    # 从成分股数据中筛选目标一级主题下的所有子主题
    target_themes = []
    for theme in data.get("themes", []):
        if theme.get("top_category") in target_categories:
            target_themes.append(theme.get("theme_name"))

    print(f"\n🎯 目标一级主题: {target_categories}  → 共 {len(target_themes)} 个子主题")

    # 3. 执行分析
    print("\n⚙️  计算主题纯度评分（5维因子）...")
    multi_result = analyze_multi_theme_purity(
        data, theme_cfg_map, stock_boards,
        target_themes=target_themes, top_n=30
    )
    print(f"   ✓ 分析完成，共 {len(multi_result['theme_purity_rankings'])} 个主题")

    # 3.1 计算一级主题综合分
    print("\n⚙️  计算一级主题综合分...")
    macro_scores = compute_macro_theme_scores(multi_result["theme_purity_rankings"])
    print(f"   ✓ 一级主题: {[m['macro_theme'] for m in macro_scores]}")

    # 4. 交易候选验证（如果有交易引擎输出）
    validated_candidates = None
    trade_file = os.path.join(CACHE_DIR, f"theme_trade_selector_{trade_date}.json")
    if os.path.exists(trade_file):
        print(f"\n🔍 验证交易候选资格...")
        validated_candidates = validate_breakout_candidates(trade_file, multi_result)

    # 5. 输出
    output = format_output(multi_result, trade_date, validated_candidates, macro_scores)

    out_json = os.path.join(CACHE_DIR, f"theme_purity_v4_{trade_date}.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n📄 JSON 已保存: {out_json}")

    # 6. 打印人类报告
    print_human_report(output, validated_candidates)


if __name__ == "__main__":
    main()
