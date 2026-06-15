#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Ranking Engine V6.1 — A股主题轮动分析引擎
==================================================

核心公式：
  ThemeScore = 0.35 × CapitalScore     （资金强度，核心）
             + 0.20 × ExpansionScore   （扩散能力，趋势）
             + 0.15 × LeaderScore      （龙头效应，锚点）
             + 0.10 × EmotionScore     （赚钱效应，辅助）
             + 0.05 × QualityScore     （主题质量，过滤）
             + 0.15 × PersistenceScore  （持续性，新增核心）

关键修正规则：
  1. 禁止重复计分 — 跨主题产业链只在主导资金主题计入
  2. AI/机构型修正 — Cap>=85 & Emo<=40 → 提升优先级
  3. 情绪退潮惩罚 — Emo>80 & Cap<65 → 判为短期热点

目标：识别未来1~5个交易日最具持续性的市场主线
       资金持续性 > 情绪爆发
"""
import json
import glob
import os
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")


def load_constituents(date_str=None):
    """加载主题成分股数据"""
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
    return data, os.path.basename(path)


def compute_subtheme_strength(sub_theme):
    """计算单个二级主题的强度（0-100），用于 ExpansionScore"""
    stocks = sub_theme.get("stocks", [])
    if not stocks:
        return 0

    total_amount = sum((s.get("avg_amount_5d") or 0) for s in stocks) / 1e8
    n_limit_up = sum(1 for s in stocks if (s.get("limit_up_days") or 0) >= 1)
    n_leaders = sum(1 for s in stocks if s.get("role") == "龙头")
    n_middles = sum(1 for s in stocks if s.get("role") == "中军")
    avg_change = sum((s.get("change_5d_pct") or 0) for s in stocks) / len(stocks)

    strength = (
        min(100, total_amount * 1.0) * 0.40
        + min(100, n_limit_up * 15) * 0.30
        + min(100, (n_leaders * 20 + n_middles * 10)) * 0.20
        + max(0, min(100, avg_change * 5)) * 0.10
    )
    return round(strength, 1)


def build_dedup_stock_map(all_themes, categories):
    """规则1：禁止重复计分 — 为每只股票找到主导资金主题

    对每只股票，找到其出现的所有一级主题中 CapitalScore 最高的那个。
    然后在其他主题中把这只股票标记为「重复」，不计入成交额。

    返回: {ts_code: primary_category}
    """
    # Step 1: 先粗算每个一级主题的「基础资金强度」（未去重，用于排序）
    primary_scores = {}
    for cat in categories:
        themes_in_cat = [t for t in all_themes if t.get("top_category") == cat]
        all_s = []
        for t in themes_in_cat:
            all_s.extend(t.get("stocks", []))
        seen = set()
        unique_amt = 0
        for s in all_s:
            code = s.get("ts_code", "")
            if code not in seen:
                seen.add(code)
                unique_amt += (s.get("avg_amount_5d") or 0) / 1e8
        primary_scores[cat] = unique_amt

    # Step 2: 遍历每只股票，记录它出现在哪些主题，选资金最强的作为主导
    stock_categories = defaultdict(list)  # ts_code -> [category1, category2, ...]
    for t in all_themes:
        cat = t.get("top_category", "其他")
        for s in t.get("stocks", []):
            code = s.get("ts_code", "")
            if code and cat not in stock_categories[code]:
                stock_categories[code].append(cat)

    # Step 3: 对每只股票选资金最强主题作为主导
    primary_map = {}
    for code, cats in stock_categories.items():
        if len(cats) == 1:
            primary_map[code] = cats[0]
        else:
            # 选资金最强的主题
            best_cat = max(cats, key=lambda c: primary_scores.get(c, 0))
            primary_map[code] = best_cat

    return primary_map


def get_unique_stocks_for_category(category, all_themes, primary_map):
    """获取指定一级主题下「去重后的股票」— 只算主导资金主题下的股票"""
    themes = [t for t in all_themes if t.get("top_category") == category]
    seen = set()
    unique = []
    for t in themes:
        for s in t.get("stocks", []):
            code = s.get("ts_code", "")
            if code in seen:
                continue
            # 这只股票的主导资金主题不是本主题 → 不计入本主题的成交额
            if primary_map.get(code) != category:
                # 依然保留为「成分股」（用于识别扩散范围），但不计入成交额
                # 这里返回一个标记
                seen.add(code)
                s_copy = dict(s)
                s_copy["_dedup_amount"] = 0  # 标记为不计入成交额
                unique.append(s_copy)
            else:
                seen.add(code)
                s_copy = dict(s)
                s_copy["_dedup_amount"] = (s.get("avg_amount_5d") or 0) / 1e8
                unique.append(s_copy)
    return unique


def compute_capital_score(category, all_themes, market_total_amount, primary_map, all_unique_market_amount):
    """CapitalScore (0-100)：资金强度 — 35%

    考虑：成交额占比（核心） + 绝对成交 + 子主题强度均值 + 资金净流入估算
    """
    themes = [t for t in all_themes if t.get("top_category") == category]
    if not themes:
        return 0, 0, 0

    unique = get_unique_stocks_for_category(category, all_themes, primary_map)
    if not unique:
        return 0, 0, 0

    # 使用去重后的成交额（_dedup_amount 已经是亿为单位）
    dedup_amount = sum(s.get("_dedup_amount", 0) for s in unique)

    # 占市场比例 — 用去重后的成交额 vs 全市场去重成交额
    market_share = (dedup_amount / all_unique_market_amount * 100) if all_unique_market_amount > 0 else 0

    # 子主题强度均值
    sub_strengths = [compute_subtheme_strength(t) for t in themes]
    avg_sub_strength = sum(sub_strengths) / len(sub_strengths) if sub_strengths else 0

    # 资金净流入估算：有上涨趋势的股票成交额占比
    up_stocks_amt = sum(s.get("_dedup_amount", 0) for s in unique if (s.get("change_5d_pct") or 0) > 0)
    net_flow_ratio = up_stocks_amt / dedup_amount * 100 if dedup_amount > 0 else 50

    # 综合：占比(35) + 绝对成交(25) + 子主题强度(25) + 净流入(15)
    cap_score = (
        min(100, market_share * 3) * 0.35
        + min(100, dedup_amount * 0.5) * 0.25
        + min(100, avg_sub_strength) * 0.25
        + min(100, net_flow_ratio) * 0.15
    )
    return round(cap_score, 1), round(dedup_amount, 1), round(market_share, 1)


def compute_emotion_score(category, all_themes, primary_map):
    """EmotionScore (0-100)：赚钱效应 — 10%（仅辅助）"""
    unique = get_unique_stocks_for_category(category, all_themes, primary_map)
    if not unique:
        return 0, {}

    n_total = len(unique) if unique else 1
    n_limit_up = sum(1 for s in unique if (s.get("limit_up_days") or 0) >= 1 and s.get("_dedup_amount", 0) > 0)
    n_consecutive = sum(1 for s in unique if (s.get("limit_up_days") or 0) >= 2 and s.get("_dedup_amount", 0) > 0)
    max_consec = max(((s.get("limit_up_days") or 0) for s in unique if s.get("_dedup_amount", 0) > 0), default=0)

    # 炸板率估算
    n_first_board = sum(1 for s in unique if (s.get("limit_up_days") or 0) == 1 and s.get("_dedup_amount", 0) > 0)
    n_bad_first = sum(1 for s in unique if (s.get("limit_up_days") or 0) == 1 and s.get("close_above_ma5") is False and s.get("_dedup_amount", 0) > 0)
    bomb_rate = (n_bad_first / n_first_board * 100) if n_first_board > 0 else 0

    # 赚钱效应：涨停密度(40) + 连板强度(35) + 炸板惩罚(25)
    limit_up_density = min(100, n_limit_up / n_total * 500)
    consecutive_score = min(100, n_consecutive * 30 + max_consec * 8)
    bomb_penalty = max(0, 100 - bomb_rate * 2)

    emo_score = (
        limit_up_density * 0.40
        + consecutive_score * 0.35
        + bomb_penalty * 0.25
    )

    detail = {
        "n_limit_up": n_limit_up,
        "n_consecutive": n_consecutive,
        "max_consec": max_consec,
        "bomb_rate_pct": round(bomb_rate, 1),
    }
    return round(emo_score, 1), detail


def compute_expansion_score(category, all_themes, market_avg_sub_strength, primary_map):
    """ExpansionScore (0-100)：扩散能力 — 20%

    衡量：二级主题数量 + 子主题强度 + 梯队完整性 + 涨停分布广度
    """
    themes = [t for t in all_themes if t.get("top_category") == category]
    if not themes:
        return 0, []

    sub_with_strength = []
    for t in themes:
        st = compute_subtheme_strength(t)
        sub_with_strength.append({
            "name": t.get("theme_name", ""),
            "strength": st,
            "n_stocks": len(t.get("stocks", [])),
            "n_limit_up": sum(1 for s in t.get("stocks", []) if (s.get("limit_up_days") or 0) >= 1),
        })

    sub_with_strength.sort(key=lambda x: -x["strength"])

    n_subs = len(sub_with_strength)
    if n_subs == 0:
        return 0, []

    # 1) 强势子主题比例（相对市场均值）
    strong_threshold = market_avg_sub_strength * 1.5
    mid_threshold = market_avg_sub_strength
    n_strong = sum(1 for s in sub_with_strength if s["strength"] >= strong_threshold)
    n_mid = sum(1 for s in sub_with_strength if mid_threshold <= s["strength"] < strong_threshold)

    # 2) 子主题平均强度 vs 市场均值
    avg_strength = sum(s["strength"] for s in sub_with_strength) / n_subs
    relative_avg = avg_strength / market_avg_sub_strength if market_avg_sub_strength > 0 else 1.0

    # 3) 扩散路径完整性：最强 vs 第二强
    if n_subs >= 2:
        top_strength = sub_with_strength[0]["strength"]
        second_strength = sub_with_strength[1]["strength"]
        tier_integrity = (second_strength / top_strength * 100) if top_strength > 0 else 0
    else:
        tier_integrity = 50

    # 4) 涨停在不同子主题间的分布广度
    sub_with_limit_up = sum(1 for s in sub_with_strength if s["n_limit_up"] >= 1)
    distribution_breadth = sub_with_limit_up / n_subs * 100

    # 综合：扩散广度(35) + 相对强度(30) + 梯队完整性(20) + 涨停分布(15)
    breadth_score = min(100, n_strong * 35 + n_mid * 15)
    relative_score = min(100, relative_avg * 40)

    exp_score = (
        breadth_score * 0.35
        + relative_score * 0.30
        + tier_integrity * 0.20
        + distribution_breadth * 0.15
    )
    return round(exp_score, 1), sub_with_strength


def compute_leader_score(category, all_themes, primary_map):
    """LeaderScore (0-100)：龙头强度 — 15%"""
    themes = [t for t in all_themes if t.get("top_category") == category]
    if not themes:
        return 0, [], []

    # 收集所有龙头/中军（只保留主导资金主题的）
    leaders = []
    middles = []
    for t in themes:
        for s in t.get("stocks", []):
            code = s.get("ts_code", "")
            # 只保留主导资金主题下的股票
            if primary_map.get(code) != category:
                continue
            info = {
                "name": s.get("name", ""),
                "ts_code": code,
                "sub_theme": t.get("theme_name", ""),
                "change_5d": s.get("change_5d_pct", 0) or 0,
                "limit_up": s.get("limit_up_days", 0) or 0,
                "amount_yi": (s.get("avg_amount_5d") or 0) / 1e8,
                "total_mv_yi": (s.get("total_mv_wan") or 0) / 10000,
                "trend_score": s.get("trend_score", 0) or 0,
                "role": s.get("role", ""),
            }
            if s.get("role") == "龙头":
                leaders.append(info)
            elif s.get("role") == "中军":
                middles.append(info)

    # 去重
    def dedupe(items):
        seen = {}
        for it in items:
            key = it["ts_code"]
            if key not in seen or it["change_5d"] > seen[key]["change_5d"]:
                seen[key] = it
        return sorted(seen.values(), key=lambda x: -x["change_5d"])

    leaders = dedupe(leaders)
    middles = dedupe(middles)

    if not leaders and not middles:
        return 20, leaders[:5], middles[:5]

    best_leaders = leaders[:3] + middles[:2]
    if not best_leaders:
        return 20, leaders[:5], middles[:5]

    avg_change = sum(l["change_5d"] for l in best_leaders) / len(best_leaders)
    avg_amount = sum(l["amount_yi"] for l in best_leaders) / len(best_leaders)
    max_consec = max((l["limit_up"] for l in best_leaders), default=0)
    avg_trend = sum(l["trend_score"] for l in best_leaders) / len(best_leaders)

    leader_score = (
        min(100, avg_change * 5) * 0.30
        + min(100, avg_amount * 2) * 0.25
        + min(100, max_consec * 25) * 0.25
        + avg_trend * 0.20
    )
    return round(leader_score, 1), leaders[:5], middles[:5]


def compute_quality_score(category, all_themes, purity_data=None, primary_map=None):
    """QualityScore (0-100)：主题质量 / 纯度 — 5%（仅过滤）"""
    if purity_data and purity_data.get(category):
        pd = purity_data[category]
        avg_purity = pd.get("avg_purity", 0)
        high_purity_ratio = pd.get("high_purity_ratio", 0)
        top_final = pd.get("top_final_score", 0)
        quality_score = (
            min(100, avg_purity) * 0.40
            + min(100, high_purity_ratio) * 0.35
            + min(100, top_final) * 0.25
        )
        return round(min(100, quality_score), 1)

    themes = [t for t in all_themes if t.get("top_category") == category]
    if not themes:
        return 0

    all_stocks = []
    for t in themes:
        all_stocks.extend(t.get("stocks", []))

    seen = set()
    unique = []
    for s in all_stocks:
        code = s.get("ts_code", "")
        if code not in seen:
            seen.add(code)
            unique.append(s)

    n = len(unique) if unique else 1
    matched = [s for s in unique if s.get("industry_match")]
    n_matched = len(matched)
    purity_of_matched = n_matched / n * 100 if n > 0 else 0

    avg_combined = sum((s.get("combined_score") or 0) for s in unique) / n
    avg_trend = sum((s.get("trend_score") or 0) for s in unique) / n
    high_purity_ratio = sum(1 for s in unique if (s.get("combined_score") or 0) >= 60) / n * 100
    n_core = sum(1 for s in unique if s.get("role") in ("龙头", "中军"))
    core_quality_ratio = n_core / n * 200 if n > 0 else 0

    quality_score = (
        min(100, purity_of_matched) * 0.25
        + min(100, avg_combined) * 0.25
        + min(100, high_purity_ratio) * 0.25
        + min(100, avg_trend) * 0.15
        + min(100, core_quality_ratio) * 0.10
    )
    return round(min(100, quality_score), 1)


def compute_persistence_score(category, all_themes, primary_map):
    """PersistenceScore (0-100)：持续性评分 — 15%（新增核心）

    用于判断主题是否「能走趋势」而不是「一日游」。
    计算：
      0.40 × 5日趋势一致性  —— change_5d_pct>0 比例 + close_above_ma5 + trend_score
      0.30 × 10日趋势延续性 —— change_10d_pct>0 比例 + ma10_slope_pct
      0.20 × 强势天数占比   —— recent_up_days / 5 的比例
      0.10 × 回撤稳定性     —— 5日/10日涨幅差距越小越稳定

    评分参考：100连续资金流入稳定走强 / 80趋势明显延续 / 60震荡上行 / 40一日波动 / 20纯情绪冲击
    """
    unique = get_unique_stocks_for_category(category, all_themes, primary_map)
    if not unique:
        return 0, {}

    # 只看主导资金主题下、有成交额的股票
    active = [s for s in unique if s.get("_dedup_amount", 0) > 0]
    n_active = len(active) if active else 1

    # ====== 1) 5日趋势一致性 ======
    n_up_5d = sum(1 for s in active if (s.get("change_5d_pct") or 0) > 0)
    n_above_ma5 = sum(1 for s in active if s.get("close_above_ma5") is True)
    avg_trend_score = sum((s.get("trend_score") or 0) for s in active) / n_active
    trend_consistency_5d = (
        n_up_5d / n_active * 100 * 0.40
        + n_above_ma5 / n_active * 100 * 0.30
        + avg_trend_score * 0.30
    )

    # ====== 2) 10日趋势延续性 ======
    n_up_10d = sum(1 for s in active if (s.get("change_10d_pct") or 0) > 0)
    avg_ma10_slope = sum((s.get("ma10_slope_pct") or 0) for s in active) / n_active
    ma10_slope_score = min(100, max(0, avg_ma10_slope * 10))  # 斜率10%以上得满分
    trend_consistency_10d = (
        n_up_10d / n_active * 100 * 0.50
        + ma10_slope_score * 0.50
    )

    # ====== 3) 强势天数占比 ======
    avg_recent_up = sum((s.get("recent_up_days") or 0) for s in active) / n_active
    strong_days_ratio = min(100, avg_recent_up / 5 * 100)

    # ====== 4) 回撤稳定性 ======
    # 5日/10日涨幅差距小 → 稳定上行；差距大 → 暴涨暴跌或回调
    avg_5d = sum((s.get("change_5d_pct") or 0) for s in active) / n_active
    avg_10d = sum((s.get("change_10d_pct") or 0) for s in active) / n_active
    gap = abs(avg_5d - avg_10d)
    # 差距<5%给100分，差距>20%给0分
    stability_score = max(0, min(100, 100 - gap * 5))

    persistence = (
        min(100, trend_consistency_5d) * 0.40
        + min(100, trend_consistency_10d) * 0.30
        + min(100, strong_days_ratio) * 0.20
        + min(100, stability_score) * 0.10
    )

    detail = {
        "trend_5d": round(trend_consistency_5d, 1),
        "trend_10d": round(trend_consistency_10d, 1),
        "strong_days": round(strong_days_ratio, 1),
        "stability": round(stability_score, 1),
        "avg_change_5d": round(avg_5d, 1),
        "avg_change_10d": round(avg_10d, 1),
        "avg_ma10_slope": round(avg_ma10_slope, 1),
        "up_5d_ratio": round(n_up_5d / n_active * 100, 1),
        "above_ma5_ratio": round(n_above_ma5 / n_active * 100, 1),
    }
    return round(persistence, 1), detail


def load_v41_purity_data(trade_date, cache_dir):
    """加载 V4.1 ThemePurity 引擎输出"""
    path = os.path.join(cache_dir, "theme_purity_v4_%s.json" % trade_date)
    if not os.path.exists(path):
        return None

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    old_rows = data.get("theme_detail_rankings", [])
    if old_rows:
        result = {}
        for theme_row in old_rows:
            cat = theme_row.get("theme_name", "")
            avg_purity = theme_row.get("avg_purity", theme_row.get("theme_purity_score", 0))
            tr = theme_row.get("tiers_ratio", {})
            core_ratio = tr.get("核心", 0) if isinstance(tr, dict) else 0
            high_purity_ratio = (tr.get("核心", 0) + tr.get("高纯度", 0)) if isinstance(tr, dict) else 0
            top_final = theme_row.get("top_final_score", 0)
            result[cat] = {
                "avg_purity": avg_purity,
                "high_purity_ratio": high_purity_ratio * 100,
                "core_ratio": core_ratio * 100,
                "top_final_score": top_final,
            }
        return result if result else None

    rows = data.get("theme_details", [])
    if not rows:
        return None

    agg = defaultdict(lambda: {
        "avg_purity_sum": 0.0, "high_purity_count": 0, "core_count": 0,
        "top_final_sum": 0.0, "total_stocks": 0, "n_subthemes": 0,
    })

    for row in rows:
        cat = row.get("top_category", row.get("theme_name", ""))
        if not cat or cat == "其他":
            continue
        avg_purity = row.get("avg_purity_score", row.get("avg_purity", 0)) or 0
        avg_final = row.get("avg_final_score", 0) or 0
        tc = row.get("tier_counts", {}) if isinstance(row.get("tier_counts"), dict) else {}
        core_n = tc.get("核心", 0) or 0
        high_n = tc.get("高纯度", 0) or 0
        total_n = sum(int(v or 0) for v in tc.values()) if tc else 0
        if total_n == 0:
            total_n = core_n + high_n + (tc.get("可交易", 0) or 0) + (tc.get("弱关联", 0) or 0) + (tc.get("伪概念", 0) or 0)

        a = agg[cat]
        a["avg_purity_sum"] += avg_purity
        a["top_final_sum"] += avg_final
        a["high_purity_count"] += high_n
        a["core_count"] += core_n
        a["total_stocks"] += total_n
        a["n_subthemes"] += 1

    result = {}
    for cat, a in agg.items():
        n = a["n_subthemes"] if a["n_subthemes"] > 0 else 1
        total = a["total_stocks"] if a["total_stocks"] > 0 else 1
        result[cat] = {
            "avg_purity": round(a["avg_purity_sum"] / n, 1),
            "high_purity_ratio": round((a["core_count"] + a["high_purity_count"]) / total * 100, 1),
            "core_ratio": round(a["core_count"] / total * 100, 1),
            "top_final_score": round(a["top_final_sum"] / n, 1),
        }

    return result if result else None


def determine_market_role(cap, emo, exp, leader_s, quality_s, pers_s, theme_score):
    """判断市场角色：主线/强轮动/短期热点/退潮

    结合 V6.1 的三大修正规则：
      - AI/机构型修正：Cap>=85 & Emo<=40 → 提升为「强轮动/观察」
      - 情绪退潮惩罚：Emo>80 & Cap<65 → 降级为短期热点
    """
    roles = []

    # 基础判断（基于综合分）
    if theme_score >= 85:
        roles.append("主线")
    elif theme_score >= 70:
        roles.append("强轮动")
    elif theme_score >= 55:
        roles.append("轮动热点")

    # 规则2：AI/机构型修正 — 资金强但情绪低 → 趋势型机会，不是情绪爆发
    if cap >= 85 and emo <= 40:
        if "主线" not in roles and "强轮动" not in roles:
            roles.append("强轮动")

    # 规则3：情绪退潮惩罚 — 情绪高但资金弱 → 短期高潮，不可作为主线
    if emo > 80 and cap < 65:
        # 清掉主线和强轮动
        roles = [r for r in roles if r not in ("主线", "强轮动")]
        if "短期热点" not in roles:
            roles.append("短期热点")

    # 持续性极低 → 判为退潮
    if pers_s < 30 and emo < 30:
        roles.append("退潮")

    if not roles:
        roles.append("观察")

    return roles


def determine_stage_v6(cap, emo, exp, leader_s, pers_s, limit_up_count, total_stock_count):
    """V6.1 资金阶段判断 — 融合持续性指标"""
    limit_up_density = limit_up_count / total_stock_count * 100 if total_stock_count > 0 else 0

    # 高潮期：情绪+龙头双高 + 涨停密度高
    if emo >= 65 and leader_s >= 60 and limit_up_density >= 3:
        return "高潮期"

    # 趋势主升期：资金+扩散+持续性三高 → 最理想状态
    if cap >= 60 and exp >= 55 and pers_s >= 55 and emo >= 40:
        return "趋势主升"

    # 主升期：均衡上升
    above_50 = sum(1 for v in [cap, emo, exp, leader_s, pers_s] if v >= 50)
    if above_50 >= 3 and emo >= 45 and cap >= 50:
        return "主升期"

    # 启动期：资金先进，情绪刚起
    if cap >= 50 and emo < 50 and leader_s < 55:
        return "启动期"

    # 分歧期：资金强但情绪弱（典型AI/机构型）
    if cap >= 55 and emo < 40:
        return "分歧期-资金强跟风弱"
    if emo >= 55 and cap < 50:
        return "分歧期-情绪强资金弱"

    # 退潮期：全弱
    if cap < 40 and emo < 40:
        return "退潮期"

    total = cap + emo + exp + pers_s
    if total >= 220:
        return "主升期"
    elif total >= 160:
        return "启动期"
    elif total >= 100:
        return "分歧期"
    else:
        return "退潮期"


def determine_next_action(cap, emo, exp, pers_s, leader_s, theme_score, stage, market_role):
    """给出下一步操作建议"""
    if "退潮" in market_role or stage == "退潮期":
        return "减仓 → 观望，等待资金重新聚集"
    if theme_score >= 85 and pers_s >= 55:
        return "可加仓 → 持续性主线，跟随资金"
    if "主线" in market_role and pers_s >= 50:
        return "持有观察 → 趋势延续，关注龙头"
    if "强轮动" in market_role:
        if cap >= 70:
            return "试探介入 → 资金驱动型，看子主题扩散"
        else:
            return "观察等待 → 轮动快，等待确认信号"
    if "短期热点" in market_role:
        return "回避 → 纯情绪驱动，缺乏资金支撑"
    if stage == "启动期":
        return "关注 → 资金已入，等情绪确认"
    if stage == "分歧期-资金强跟风弱":
        return "布局低位 → 资金潜伏，等待散户觉醒"
    if stage == "分歧期-情绪强资金弱":
        return "观望 → 情绪高涨但资金不跟进，易冲高回落"
    return "观察 → 等待明确信号"


def collect_laggings_v6(category, all_themes, primary_map, top_n=5):
    """V6.1 补涨候选：只看主导资金主题下的股票"""
    themes = [t for t in all_themes if t.get("top_category") == category]
    lagging_candidates = []
    seen = set()
    for t in themes:
        for s in t.get("stocks", []):
            code = s.get("ts_code", "")
            if code in seen:
                continue
            if primary_map.get(code) != category:
                continue  # 不是本主题的主导资金股
            seen.add(code)

            change = s.get("change_5d_pct") or 0
            amount = (s.get("avg_amount_5d") or 0) / 1e8
            limit_up = s.get("limit_up_days") or 0
            ma10_slope = s.get("ma10_slope_pct") or 0
            above_ma5 = s.get("close_above_ma5", False)

            if change < 15 and (amount >= 1 or ma10_slope > 0 or above_ma5):
                breakout_signal = 0
                if limit_up >= 1:
                    breakout_signal += 40
                if above_ma5:
                    breakout_signal += 20
                if ma10_slope > 2:
                    breakout_signal += 20
                if amount >= 5:
                    breakout_signal += 20
                lagging_candidates.append({
                    "name": s.get("name", ""),
                    "ts_code": code,
                    "sub_theme": t.get("theme_name", ""),
                    "change_5d": round(change, 1),
                    "amount_yi": round(amount, 2),
                    "limit_up": limit_up,
                    "ma10_slope": round(ma10_slope, 1),
                    "signal_score": breakout_signal,
                    "role": s.get("role", ""),
                })

    lagging_candidates.sort(key=lambda x: -x["signal_score"])
    return lagging_candidates[:top_n]


def main():
    # 加载成分股数据
    data, filename = load_constituents()
    if not data:
        print("未找到成分股数据")
        return
    trade_date = data.get("trade_date", "unknown")
    all_themes = data.get("themes", [])

    print("=== Theme Ranking Engine V6.1 ===")
    print("数据: %s" % filename)
    print("主题总数: %d" % len(all_themes))

    # 按一级主题分类
    categories = defaultdict(list)
    for t in all_themes:
        cat = t.get("top_category", "其他")
        categories[cat].append(t)

    # Step 1: 构建去重产业链映射 —— 规则1
    primary_map = build_dedup_stock_map(all_themes, categories)

    # 全市场去重成交额（用于占比计算）
    all_unique_market_amount = 0
    for code, primary_cat in primary_map.items():
        # 找到这只股票的成交额
        for t in all_themes:
            for s in t.get("stocks", []):
                if s.get("ts_code", "") == code:
                    all_unique_market_amount += (s.get("avg_amount_5d") or 0) / 1e8
                    break
            else:
                continue
            break

    # 全市场子主题强度均值
    all_sub_strengths = [compute_subtheme_strength(t) for t in all_themes]
    market_avg_sub_strength = sum(all_sub_strengths) / len(all_sub_strengths) if all_sub_strengths else 30

    # 尝试加载 V4.1 纯度数据
    v41_purity = load_v41_purity_data(trade_date, CACHE_DIR)
    has_v41 = v41_purity is not None and len(v41_purity) > 0

    print("全市场子主题强度均值: %.1f" % market_avg_sub_strength)
    print("全市场去重成交额: %.1f 亿" % all_unique_market_amount)
    print("V4.1纯度数据: %s" % ("已加载" if has_v41 else "未找到（使用代理计算）"))
    print()

    # 对每个一级主题评分
    results = []
    for category in categories:
        # 六大因子
        cap_score, amount_yi, market_share = compute_capital_score(
            category, all_themes, market_total_amount=all_unique_market_amount,
            primary_map=primary_map, all_unique_market_amount=all_unique_market_amount
        )
        emo_score, emo_detail = compute_emotion_score(category, all_themes, primary_map)
        exp_score, sub_strengths = compute_expansion_score(
            category, all_themes, market_avg_sub_strength, primary_map
        )
        leader_score, leaders, middles = compute_leader_score(category, all_themes, primary_map)
        quality_score = compute_quality_score(category, all_themes, v41_purity, primary_map)
        persistence_score, persistence_detail = compute_persistence_score(
            category, all_themes, primary_map
        )

        # V6.1 核心公式
        theme_score_raw = (
            cap_score * 0.35
            + exp_score * 0.20
            + leader_score * 0.15
            + emo_score * 0.10
            + quality_score * 0.05
            + persistence_score * 0.15
        )

        # 规则2：AI/机构型修正 — Cap>=85 & Emo<=40 → 提升
        boost = 0
        penalty = 0
        if cap_score >= 85 and emo_score <= 40:
            boost = round(min(theme_score_raw * 0.08, 8), 1)  # 最多+8
        # 规则3：情绪退潮惩罚 — Emo>80 & Cap<65 → 降级
        if emo_score > 80 and cap_score < 65:
            penalty = round(min(theme_score_raw * 0.15, 15), 1)  # 最多-15

        theme_score = round(theme_score_raw + boost - penalty, 1)

        n_stocks_in_cat = len(set(
            s.get("ts_code", "") for t in categories[category]
            for s in t.get("stocks", []) if primary_map.get(s.get("ts_code", "")) == category
        ))
        stage = determine_stage_v6(
            cap_score, emo_score, exp_score, leader_score, persistence_score,
            emo_detail.get("n_limit_up", 0), n_stocks_in_cat if n_stocks_in_cat > 0 else 1
        )

        market_role = determine_market_role(
            cap_score, emo_score, exp_score, leader_score, quality_score,
            persistence_score, theme_score
        )

        next_action = determine_next_action(
            cap_score, emo_score, exp_score, persistence_score,
            leader_score, theme_score, stage, market_role
        )

        # Top3子主题
        main_subthemes = [s for s in sub_strengths[:3]]
        leaders_top = [
            {"name": l["name"], "ts_code": l["ts_code"], "change_5d": l["change_5d"],
             "amount_yi": l["amount_yi"], "limit_up": l["limit_up"], "sub_theme": l["sub_theme"]}
            for l in leaders[:5]
        ]
        middles_top = [
            {"name": m["name"], "ts_code": m["ts_code"], "change_5d": m["change_5d"],
             "amount_yi": m["amount_yi"], "limit_up": m["limit_up"], "sub_theme": m["sub_theme"]}
            for m in middles[:5]
        ]
        laggings = collect_laggings_v6(category, all_themes, primary_map, top_n=5)

        reason_parts = []
        reason_parts.append("成交%.1f亿(占比%.1f%%)" % (amount_yi, market_share))
        reason_parts.append("涨停%d只(连板%d,最高%d板)" % (
            emo_detail.get("n_limit_up", 0),
            emo_detail.get("n_consecutive", 0),
            emo_detail.get("max_consec", 0),
        ))
        reason_parts.append("持续性%.1f分(5日趋势%.1f/10日趋势%.1f/强势天%.1f/稳定%.1f)" % (
            persistence_score, persistence_detail["trend_5d"],
            persistence_detail["trend_10d"], persistence_detail["strong_days"],
            persistence_detail["stability"],
        ))
        reason_parts.append("强势子主题%d个" % sum(1 for s in sub_strengths if s["strength"] >= 40))
        if boost > 0:
            reason_parts.append("AI资金驱动(+%.1f分)" % boost)
        if penalty > 0:
            reason_parts.append("情绪透支(-%.1f分)" % penalty)

        results.append({
            "theme": category,
            "theme_score": theme_score,
            "theme_score_raw": round(theme_score_raw, 1),
            "capital_score": cap_score,
            "emotion_score": emo_score,
            "expansion_score": exp_score,
            "leader_score": leader_score,
            "quality_score": quality_score,
            "persistence_score": persistence_score,
            "stage": stage,
            "market_role": market_role,
            "next_action": next_action,
            "reason": "；".join(reason_parts),
            "n_subthemes": len(categories[category]),
            "n_stocks": n_stocks_in_cat,
            "amount_yi": amount_yi,
            "market_share_pct": market_share,
            "n_limit_up": emo_detail.get("n_limit_up", 0),
            "n_consecutive": emo_detail.get("n_consecutive", 0),
            "max_consec": emo_detail.get("max_consec", 0),
            "main_subthemes": main_subthemes,
            "leaders": leaders_top,
            "middles": middles_top,
            "laggings": laggings,
            "persistence_detail": persistence_detail,
            "boost": boost,
            "penalty": penalty,
        })

    # 按 theme_score 降序排名
    results.sort(key=lambda x: -x["theme_score"])
    for i, r in enumerate(results, 1):
        r["rank"] = i

    # 保存 JSON
    output = {
        "trade_date": trade_date,
        "engine": "Theme Ranking Engine V6.1",
        "market_total_amount_yi": round(all_unique_market_amount, 1),
        "total_categories": len(results),
        "formula": "ThemeScore = 0.35*Capital + 0.20*Expansion + 0.15*Leader + 0.10*Emotion + 0.05*Quality + 0.15*Persistence",
        "correction_rules": [
            "1. 禁止重复计分：跨主题产业链只在主导资金主题计入",
            "2. AI/机构型修正：Cap>=85 & Emo<=40 → 加8%",
            "3. 情绪退潮惩罚：Emo>80 & Cap<65 → 扣15%"
        ],
        "rankings": results,
        "market_mainlines": [r for r in results if "主线" in r["market_role"] or "强轮动" in r["market_role"]][:5],
    }

    out_json = os.path.join(CACHE_DIR, "theme_ranking_v6_1_%s.json" % trade_date)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("JSON已保存: %s" % out_json)
    print()

    # 控制台打印 - 六大因子表
    print("=" * 130)
    print("%s  一级主题综合评分 V6.1  (资金优先·持续优先·龙头优先)" % trade_date)
    print("=" * 130)

    header = "%-3s %-12s %7s  %6s %6s %6s %6s %6s %6s  %-14s %-16s" % (
        "排名", "主题", "综合分", "资金", "扩散", "龙头", "情绪", "纯度", "持续",
        "阶段", "市场角色"
    )
    print(header)
    print("-" * 130)

    for r in results:
        line = "%-3d %-12s %7.1f  %6.1f %6.1f %6.1f %6.1f %6.1f %6.1f  %-14s %s" % (
            r["rank"], r["theme"], r["theme_score"],
            r["capital_score"], r["expansion_score"], r["leader_score"],
            r["emotion_score"], r["quality_score"], r["persistence_score"],
            r["stage"], "/".join(r["market_role"])
        )
        print(line)

    print()
    print("=" * 130)
    print("TOP 3 主题详情  (资金持续性 > 情绪爆发)")
    print("=" * 130)

    for r in results[:3]:
        print("\n▶ [%d] %s  综合分%.1f (原始%.1f) | %s | %s" % (
            r["rank"], r["theme"], r["theme_score"], r["theme_score_raw"],
            "/".join(r["market_role"]), r["stage"]
        ))
        print("  → 操作建议: %s" % r["next_action"])
        print("  六大因子: 资金=%.1f 扩散=%.1f 龙头=%.1f 情绪=%.1f 纯度=%.1f 持续=%.1f" % (
            r["capital_score"], r["expansion_score"], r["leader_score"],
            r["emotion_score"], r["quality_score"], r["persistence_score"]
        ))
        print("  主题规模: %d个子主题, %d只股票, 5日均成交%.1f亿(市场占比%.1f%%)" % (
            r["n_subthemes"], r["n_stocks"], r["amount_yi"], r["market_share_pct"]
        ))
        print("  赚钱效应: 涨停%d只, 连板%d只, 最高%d板" % (
            r["n_limit_up"], r["n_consecutive"], r["max_consec"]
        ))
        pd = r["persistence_detail"]
        print("  持续性拆解: 5日趋势%.1f | 10日趋势%.1f | 强势天数%.1f | 回撤稳定%.1f" % (
            pd["trend_5d"], pd["trend_10d"], pd["strong_days"], pd["stability"]
        ))
        print("  核心子主题:")
        for s in r["main_subthemes"]:
            print("    • %s (强度%.1f, %d只股, %d涨停)" % (
                s["name"], s["strength"], s["n_stocks"], s["n_limit_up"]
            ))
        print("  龙头TOP5:")
        for l in r["leaders"]:
            print("    • %s(%s) 5日+%.1f%% 成交%.1f亿 %s连板 [%s]" % (
                l["name"], l["ts_code"], l["change_5d"], l["amount_yi"],
                ("%d" % l["limit_up"]) if l["limit_up"] else "无", l["sub_theme"]
            ))
        print("  中军TOP5:")
        for m in r["middles"]:
            print("    • %s(%s) 5日+%.1f%% 成交%.1f亿 [%s]" % (
                m["name"], m["ts_code"], m["change_5d"], m["amount_yi"], m["sub_theme"]
            ))
        print("  补涨候选TOP5:")
        for lag in r["laggings"]:
            print("    • %s(%s) 5日+%.1f%% 成交%.1f亿 MA10斜率+%.1f%% [%s] 信号分%d" % (
                lag["name"], lag["ts_code"], lag["change_5d"], lag["amount_yi"],
                lag["ma10_slope"], lag["sub_theme"], lag["signal_score"]
            ))
        print("  综合理由: %s" % r["reason"])

    print()
    print("=" * 130)
    print("V6.1 关键说明:")
    print("  1. 产业链去重 — 同一只股票只在资金最强的一级主题计入成交额")
    print("  2. 资金权重35% — 成交额是主线判断的第一要素")
    print("  3. 持续性权重15% — 过滤掉一日游情绪板")
    print("  4. 情绪仅10% — 避免被涨停数量误导")
    print("  5. AI资金驱动修正 — Cap>=85 & Emo<=40 的趋势型主题被加分")
    print("  6. 情绪透支惩罚 — Emo>80 & Cap<65 的纯情绪主题被减分")
    print("分析完成。")


if __name__ == "__main__":
    main()
