#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Ranking Engine V5 — 一级主题综合评分引擎
================================================

综合评分：
  ThemeScore = 0.40 × CapitalScore + 0.25 × ExpansionScore
             + 0.20 × LeaderScore + 0.10 × EmotionScore
             + 0.05 × QualityScore

目标：识别未来1~5个交易日最具持续性的市场主线
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

    # 子主题强度 = 成交(40) + 涨停(30) + 龙头中军(20) + 涨幅(10)
    strength = (
        min(100, total_amount * 1.0) * 0.40
        + min(100, n_limit_up * 15) * 0.30
        + min(100, (n_leaders * 20 + n_middles * 10)) * 0.20
        + max(0, min(100, avg_change * 5)) * 0.10
    )
    return round(strength, 1)


def compute_capital_score(category, all_themes, market_total_amount):
    """CapitalScore (0-100)：资金强度 — 30%"""
    themes = [t for t in all_themes if t.get("top_category") == category]
    if not themes:
        return 0, 0, 0

    # 1. 成交额总量（该一级主题下所有子主题股票之和）
    all_stocks = []
    for t in themes:
        all_stocks.extend(t.get("stocks", []))

    total_amount = sum((s.get("avg_amount_5d") or 0) for s in all_stocks) / 1e8

    # 去重股票：同一股票在多个子主题只算一次
    seen_codes = set()
    unique_stocks = []
    unique_amount = 0
    for s in all_stocks:
        code = s.get("ts_code", "")
        if code not in seen_codes:
            seen_codes.add(code)
            unique_stocks.append(s)
            unique_amount += (s.get("avg_amount_5d") or 0) / 1e8

    # 占市场比例
    market_share = (unique_amount / market_total_amount * 100) if market_total_amount > 0 else 0

    # 子主题强度求和
    sub_strengths = [compute_subtheme_strength(t) for t in themes]
    avg_sub_strength = sum(sub_strengths) / len(sub_strengths) if sub_strengths else 0

    # 综合：占比(40) + 绝对成交(35) + 子主题平均强度(25)
    cap_score = (
        min(100, market_share * 3) * 0.40
        + min(100, unique_amount * 0.5) * 0.35
        + min(100, avg_sub_strength) * 0.25
    )
    return round(cap_score, 1), round(unique_amount, 1), round(market_share, 1)


def compute_emotion_score(category, all_themes):
    """EmotionScore (0-100)：赚钱效应 — 25%"""
    themes = [t for t in all_themes if t.get("top_category") == category]
    if not themes:
        return 0, {}

    all_stocks = []
    for t in themes:
        all_stocks.extend(t.get("stocks", []))

    # 去重股票
    seen = set()
    unique = []
    for s in all_stocks:
        code = s.get("ts_code", "")
        if code not in seen:
            seen.add(code)
            unique.append(s)

    n_total = len(unique) if unique else 1
    n_limit_up = sum(1 for s in unique if (s.get("limit_up_days") or 0) >= 1)
    n_consecutive = sum(1 for s in unique if (s.get("limit_up_days") or 0) >= 2)
    max_consec = max(((s.get("limit_up_days") or 0) for s in unique), default=0)

    # 炸板率估算：连板1次(首板)中 MA5 在下方的比例
    n_first_board = sum(1 for s in unique if (s.get("limit_up_days") or 0) == 1)
    n_bad_first = sum(1 for s in unique if (s.get("limit_up_days") or 0) == 1 and s.get("close_above_ma5") is False)
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


def compute_expansion_score(category, all_themes, market_avg_sub_strength):
    """ExpansionScore (0-100)：扩散能力 — 20%
    v2 优化：使用相对指标，避免全部接近100
    """
    themes = [t for t in all_themes if t.get("top_category") == category]
    if not themes:
        return 0, []

    # 每个子主题计算强度
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

    # 1) 强势子主题比例 (相对市场均值) — 强度 >= 市场均值*1.5 算强势
    strong_threshold = market_avg_sub_strength * 1.5
    mid_threshold = market_avg_sub_strength
    n_strong = sum(1 for s in sub_with_strength if s["strength"] >= strong_threshold)
    n_mid = sum(1 for s in sub_with_strength if mid_threshold <= s["strength"] < strong_threshold)

    # 2) 子主题平均强度 vs 市场均值 (相对倍数，0-100映射)
    avg_strength = sum(s["strength"] for s in sub_with_strength) / n_subs
    relative_avg = avg_strength / market_avg_sub_strength if market_avg_sub_strength > 0 else 1.0

    # 3) 扩散路径完整性：最强 vs 最弱子主题的强度差距
    # 如果最强主题和次强主题差距巨大 → 单点爆发；如果差距小 → 真正的扩散
    if n_subs >= 2:
        top_strength = sub_with_strength[0]["strength"]
        second_strength = sub_with_strength[1]["strength"]
        # 梯队完整性：第二名 >= 第一名的70% 视为扩散良好
        tier_integrity = (second_strength / top_strength * 100) if top_strength > 0 else 0
    else:
        tier_integrity = 50  # 只有一个子主题给中性分

    # 4) 涨停在不同子主题间的分布广度（而不是单纯的总数）
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


def compute_leader_score(category, all_themes):
    """LeaderScore (0-100)：龙头强度 — 15%"""
    themes = [t for t in all_themes if t.get("top_category") == category]
    if not themes:
        return 0, [], []

    # 收集所有龙头/中军
    leaders = []
    middles = []
    for t in themes:
        for s in t.get("stocks", []):
            info = {
                "name": s.get("name", ""),
                "ts_code": s.get("ts_code", ""),
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

    # 去重（同一只股票在多个主题只留最强的）
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

    # 龙头综合分：Top龙头的强度
    best_leaders = leaders[:3] + middles[:2]
    if not best_leaders:
        return 20, leaders[:5], middles[:5]

    # 龙头涨幅
    avg_change = sum(l["change_5d"] for l in best_leaders) / len(best_leaders)
    # 龙头成交额
    avg_amount = sum(l["amount_yi"] for l in best_leaders) / len(best_leaders)
    # 连板高度
    max_consec = max((l["limit_up"] for l in best_leaders), default=0)
    # 趋势分
    avg_trend = sum(l["trend_score"] for l in best_leaders) / len(best_leaders)

    leader_score = (
        min(100, avg_change * 5) * 0.30
        + min(100, avg_amount * 2) * 0.25
        + min(100, max_consec * 25) * 0.25
        + avg_trend * 0.20
    )
    return round(leader_score, 1), leaders[:5], middles[:5]


def compute_quality_score(category, all_themes, purity_data=None):
    """QualityScore (0-100)：主题质量 / 纯度 — 10%
    v2 优化：优先使用 V4.1 ThemePurity 引擎数据，否则用成分股 combined_score
    """
    themes = [t for t in all_themes if t.get("top_category") == category]
    if not themes:
        return 0

    # 方案A：已有 V4.1 纯度数据 — 直接使用
    if purity_data and purity_data.get(category):
        pd = purity_data[category]
        avg_purity = pd.get("avg_purity", 0)
        high_purity_ratio = pd.get("high_purity_ratio", 0)
        core_ratio = pd.get("core_ratio", 0)
        top_final = pd.get("top_final_score", 0)

        # V4.1 融合：均值纯度(40) + 高纯度占比(35) + 最优股FinalScore(25)
        quality_score = (
            min(100, avg_purity) * 0.40
            + min(100, high_purity_ratio) * 0.35
            + min(100, top_final) * 0.25
        )
        return round(min(100, quality_score), 1)

    # 方案B：无 V4.1 数据 — 使用成分股 combined_score 代理
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

    # 按行业匹配(industry_match=True)过滤纯概念股
    matched = [s for s in unique if s.get("industry_match")]
    n_matched = len(matched)
    purity_of_matched = n_matched / n * 100 if n > 0 else 0

    avg_combined = sum((s.get("combined_score") or 0) for s in unique) / n
    avg_trend = sum((s.get("trend_score") or 0) for s in unique) / n
    high_purity_ratio = sum(1 for s in unique if (s.get("combined_score") or 0) >= 60) / n * 100

    # 龙头/中军质量分（而不是纯补涨股的比例）
    n_core = sum(1 for s in unique if s.get("role") in ("龙头", "中军"))
    core_quality_ratio = n_core / n * 200 if n > 0 else 0  # 放大权重

    quality_score = (
        min(100, purity_of_matched) * 0.25
        + min(100, avg_combined) * 0.25
        + min(100, high_purity_ratio) * 0.25
        + min(100, avg_trend) * 0.15
        + min(100, core_quality_ratio) * 0.10
    )
    return round(min(100, quality_score), 1)


def load_v41_purity_data(trade_date, cache_dir):
    """加载 V4.1 ThemePurity 引擎输出（如有）
    支持两种结构：
      1. theme_detail_rankings: [{theme_name, avg_purity, tiers_ratio, ...}]
      2. theme_details: [{theme_name, top_category, avg_purity_score, tier_counts, ...}]
    返回 {一级主题: {avg_purity, high_purity_ratio, core_ratio, top_final_score}}
    """
    path = os.path.join(cache_dir, "theme_purity_v4_%s.json" % trade_date)
    if not os.path.exists(path):
        return None

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 方案1: 旧格式 theme_detail_rankings
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

    # 方案2: 新格式 theme_details — 按 top_category 聚合所有子主题
    rows = data.get("theme_details", [])
    if not rows:
        return None

    # 聚合结构: {top_category: {avg_purity_sum, high_purity_sum, core_sum, top_final_sum, total_stocks, count}}
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


def determine_stage(cap, emo, exp, leader_s, quality_s, limit_up_count, total_stock_count):
    """v2 优化：根据五大因子判断资金阶段 — 增强区分度
    关键判断：
    - 高潮期：情绪龙头双高
    - 主升期：资金情绪双升
    - 启动期：资金先进，情绪刚起
    - 分歧期：资金强/情绪弱（典型分歧）
    - 退潮期：全弱
    """
    # 涨停密度(%)
    limit_up_density = limit_up_count / total_stock_count * 100 if total_stock_count > 0 else 0

    # === 高潮期：情绪 AND 龙头 双高
    if emo >= 65 and leader_s >= 60 and limit_up_density >= 3:
        return "高潮期"

    # === 主升期：均衡上升
    # 资金 + 情绪 + 扩散三者均中等偏上，且至少两项 >= 50
    above_50 = sum(1 for v in [cap, emo, exp, leader_s] if v >= 50)
    if above_50 >= 3 and emo >= 45 and cap >= 50:
        return "主升期"

    # === 启动期：资金已进入但情绪/扩散还没起来
    # cap >= 50 但 emo < 50 且 leader_s < 55
    if cap >= 50 and emo < 50 and leader_s < 55:
        return "启动期"

    # === 分歧期：资金强但情绪弱 或者 情绪强但扩散不足
    if cap >= 55 and emo < 40:
        return "分歧期-资金强跟风弱"
    if emo >= 55 and cap < 50:
        return "分歧期-情绪强资金弱"

    # === 低潮期：资金流出迹象
    if cap < 40 and emo < 40:
        return "退潮期"

    # 降级版分层兜底
    total = cap + emo + exp
    if total >= 180:
        return "主升期"
    elif total >= 130:
        return "启动期"
    elif total >= 80:
        return "分歧期"
    else:
        return "退潮期"


def determine_mainline_level(theme_score):
    """主线级别判断"""
    if theme_score >= 85:
        return "市场主线"
    elif theme_score >= 75:
        return "强势主线"
    elif theme_score >= 65:
        return "轮动热点"
    elif theme_score >= 55:
        return "观察方向"
    else:
        return "非主流方向"


def collect_laggings(category, all_themes, top_n=5):
    """收集补涨候选：低涨幅但有异动的股票"""
    themes = [t for t in all_themes if t.get("top_category") == category]
    lagging_candidates = []
    seen = set()
    for t in themes:
        for s in t.get("stocks", []):
            code = s.get("ts_code", "")
            if code in seen:
                continue
            seen.add(code)
            change = s.get("change_5d_pct") or 0
            amount = (s.get("avg_amount_5d") or 0) / 1e8
            limit_up = s.get("limit_up_days") or 0
            ma10_slope = s.get("ma10_slope_pct") or 0
            above_ma5 = s.get("close_above_ma5", False)

            # 补涨特征：涨幅 < 15% 但有资金/趋势异动
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


def predict_next_wave(leaders, middles, laggings, sub_strengths):
    """预测下一阶段扩散方向"""
    # 最强子主题名称
    top_sub = sub_strengths[0]["name"] if sub_strengths else "未知"
    top_2_sub = sub_strengths[1]["name"] if len(sub_strengths) > 1 else ""

    # 如果龙头已经高，看补涨扩散
    top_leader_change = max((l["change_5d"] for l in leaders[:2]), default=0) if leaders else 0

    if top_leader_change >= 20 and laggings:
        return "龙头已强势 → 资金向%s补涨扩散" % (laggings[0]["sub_theme"])
    elif top_leader_change >= 10 and top_2_sub:
        return "%s主升中 → 下一步扩散至%s" % (top_sub, top_2_sub)
    elif sub_strengths and sub_strengths[0]["strength"] >= 40:
        return "%s为核心战场，资金持续流入" % top_sub
    else:
        return "主题内轮动，关注新热点子主题"


def determine_capital_flow(cap, emo, exp, sub_strengths):
    """资金流向判断"""
    top_sub = sub_strengths[0]["name"] if sub_strengths else "未知"
    if cap >= 60 and emo >= 50:
        return "净流入 → %s为主战场" % top_sub
    elif cap >= 40 and emo >= 30:
        return "温和流入 → 关注%s子主题" % top_sub
    elif cap < 30 and emo < 30:
        return "净流出 → 观望"
    else:
        return "资金观望 → 等待方向选择"


def main():
    # 加载成分股数据
    data, filename = load_constituents()
    if not data:
        print("未找到成分股数据")
        return
    trade_date = data.get("trade_date", "unknown")
    all_themes = data.get("themes", [])

    print("=== Theme Ranking Engine V5 ===")
    print("数据: %s" % filename)
    print("主题总数: %d" % len(all_themes))

    # 按一级主题分类
    categories = defaultdict(list)
    for t in all_themes:
        cat = t.get("top_category", "其他")
        categories[cat].append(t)

    # 计算全市场总成交额（所有去重股票）
    market_stocks = []
    seen = set()
    for t in all_themes:
        for s in t.get("stocks", []):
            code = s.get("ts_code", "")
            if code not in seen:
                seen.add(code)
                market_stocks.append(s)
    market_total_amount = sum((s.get("avg_amount_5d") or 0) for s in market_stocks) / 1e8

    # 全市场子主题强度均值（用于相对指标）
    all_sub_strengths = [compute_subtheme_strength(t) for t in all_themes]
    market_avg_sub_strength = sum(all_sub_strengths) / len(all_sub_strengths) if all_sub_strengths else 30
    all_sub_strengths_sorted = sorted(all_sub_strengths, reverse=True)
    top3_avg = sum(all_sub_strengths_sorted[:3]) / 3 if len(all_sub_strengths_sorted) >= 3 else market_avg_sub_strength

    # 尝试加载 V4.1 纯度数据
    v41_purity = load_v41_purity_data(trade_date, CACHE_DIR)
    has_v41 = v41_purity is not None and len(v41_purity) > 0

    print("全市场去重股票: %d 只" % len(market_stocks))
    print("全市场5日均成交额: %.1f 亿" % market_total_amount)
    print("全市场子主题强度均值: %.1f" % market_avg_sub_strength)
    print("V4.1纯度数据: %s" % ("已加载" if has_v41 else "未找到（使用代理计算）"))
    print()

    # 对每个一级主题评分
    results = []
    for category in categories:
        cap_score, amount_yi, market_share = compute_capital_score(category, all_themes, market_total_amount)
        emo_score, emo_detail = compute_emotion_score(category, all_themes)
        exp_score, sub_strengths = compute_expansion_score(category, all_themes, market_avg_sub_strength)
        leader_score, leaders, middles = compute_leader_score(category, all_themes)
        quality_score = compute_quality_score(category, all_themes, v41_purity)

        theme_score = round(
            cap_score * 0.40
            + exp_score * 0.25
            + leader_score * 0.20
            + emo_score * 0.10
            + quality_score * 0.05
            , 1
        )

        n_subthemes = len(categories[category])
        n_stocks_in_cat = len(set(s.get("ts_code", "") for t in categories[category] for s in t.get("stocks", [])))
        stage = determine_stage(
            cap_score, emo_score, exp_score, leader_score, quality_score,
            emo_detail.get("n_limit_up", 0), n_stocks_in_cat
        )
        mainline_level = determine_mainline_level(theme_score)

        # Top3子主题（按强度）
        main_subthemes = [s for s in sub_strengths[:3]]

        # 龙头Top5 / 中军Top5
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

        # 补涨候选
        laggings = collect_laggings(category, all_themes, top_n=5)

        # 资金流向与下一波预测
        capital_flow = determine_capital_flow(cap_score, emo_score, exp_score, sub_strengths)
        next_wave = predict_next_wave(leaders, middles, laggings, sub_strengths)

        reason_parts = []
        reason_parts.append("成交%.1f亿(占比%.1f%%)" % (amount_yi, market_share))
        reason_parts.append("涨停%d只(连板%d,最高%d板)" % (
            emo_detail.get("n_limit_up", 0),
            emo_detail.get("n_consecutive", 0),
            emo_detail.get("max_consec", 0),
        ))
        reason_parts.append("强势子主题%d个" % sum(1 for s in sub_strengths if s["strength"] >= 40))
        reason_parts.append("纯度%.1f分" % quality_score)

        results.append({
            "theme": category,
            "theme_score": theme_score,
            "capital_score": cap_score,
            "emotion_score": emo_score,
            "expansion_score": exp_score,
            "leader_score": leader_score,
            "quality_score": quality_score,
            "stage": stage,
            "mainline_level": mainline_level,
            "n_subthemes": len(categories[category]),
            "n_stocks": len(set(s.get("ts_code", "") for t in categories[category] for s in t.get("stocks", []))),
            "amount_yi": amount_yi,
            "market_share_pct": market_share,
            "n_limit_up": emo_detail.get("n_limit_up", 0),
            "n_consecutive": emo_detail.get("n_consecutive", 0),
            "max_consec": emo_detail.get("max_consec", 0),
            "main_subthemes": main_subthemes,
            "leaders": leaders_top,
            "middles": middles_top,
            "laggings": laggings,
            "capital_flow_direction": capital_flow,
            "next_wave_prediction": next_wave,
            "reason": "；".join(reason_parts),
        })

    # 按 theme_score 降序排名
    results.sort(key=lambda x: -x["theme_score"])
    for i, r in enumerate(results, 1):
        r["rank"] = i

    # 保存 JSON
    output = {
        "trade_date": trade_date,
        "engine": "Theme Ranking Engine V5",
        "market_total_amount_yi": round(market_total_amount, 1),
        "total_categories": len(results),
        "formula": "ThemeScore = 0.40*Capital + 0.25*Expansion + 0.20*Leader + 0.10*Emotion + 0.05*Quality",
        "rankings": results,
        "top5_market_mainlines": [r for r in results if r["theme_score"] >= 75][:5],
    }

    out_json = os.path.join(CACHE_DIR, "theme_ranking_v5_%s.json" % trade_date)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("JSON已保存: %s" % out_json)
    print()

    # 控制台打印
    print("=" * 110)
    print("%s  一级主题综合评分 V5" % trade_date)
    print("=" * 110)

    header = "%-3s %-12s %6s  %5s %5s %5s %5s %5s  %-10s  %-10s" % (
        "排名", "一级主题", "综合分", "资金", "情绪", "扩散", "龙头", "纯度", "主线级别", "资金阶段"
    )
    print(header)
    print("-" * 110)

    for r in results:
        line = "%-3d %-12s %6.1f  %5.1f %5.1f %5.1f %5.1f %5.1f  %-10s  %-10s" % (
            r["rank"], r["theme"], r["theme_score"],
            r["capital_score"], r["emotion_score"], r["expansion_score"],
            r["leader_score"], r["quality_score"],
            r["mainline_level"], r["stage"]
        )
        print(line)

    print()
    print("=" * 110)
    print("TOP 3 主题详情")
    print("=" * 110)

    for r in results[:3]:
        print("\n▶ [%d] %s  综合分%.1f | %s | %s" % (
            r["rank"], r["theme"], r["theme_score"], r["mainline_level"], r["stage"]
        ))
        print("  主题规模: %d个子主题, %d只股票, 5日均成交%.1f亿(市场占比%.1f%%)" % (
            r["n_subthemes"], r["n_stocks"], r["amount_yi"], r["market_share_pct"]
        ))
        print("  赚钱效应: 涨停%d只, 连板%d只, 最高%d板" % (
            r["n_limit_up"], r["n_consecutive"], r["max_consec"]
        ))
        print("  五因子: 资金=%.1f 情绪=%.1f 扩散=%.1f 龙头=%.1f 纯度=%.1f" % (
            r["capital_score"], r["emotion_score"], r["expansion_score"],
            r["leader_score"], r["quality_score"]
        ))
        print("  核心子主题:")
        for s in r["main_subthemes"]:
            print("    • %s (强度%.1f, %d只股, %d涨停)" % (s["name"], s["strength"], s["n_stocks"], s["n_limit_up"]))
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
        print("  资金流向: %s" % r["capital_flow_direction"])
        print("  下一波预测: %s" % r["next_wave_prediction"])
        print("  综合理由: %s" % r["reason"])

    print()
    print("=" * 110)
    print("分析完成。")


if __name__ == "__main__":
    main()
