#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Graph & Capital Flow Engine V3
=====================================
不是主题分类 → 而是构建：
   资金在 一级主题 → 二级主题 → 个股 之间的流动路径
   以及预测资金扩散方向与强度。

核心输出结构：
 {
   macro_theme_graph       # 一级主题图谱 + 状态 + 共振/吸血关系
   sub_theme_flow          # 二级主题资金流向 + 龙头/中军/补涨分布
   capital_flow_paths      # 具体资金路径（带强度和解读）
   next_hotspot_prediction # 下一阶段资金可能切换的方向
 }

运行方式: python theme_capital_flow_v3.py
         （可选传入 trade_date，默认自动识别最新 JSON）
"""
import json
import os
import sys
import glob
from collections import defaultdict
from datetime import datetime

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")


# ============================================================
# 工具函数
# ============================================================

def find_latest_constituents_json():
    """在 cache 目录找最新的 theme3_constituents_YYYYMMDD.json"""
    pattern = os.path.join(CACHE_DIR, "theme3_constituents_*.json")
    files = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError(f"未找到 theme3_constituents_*.json，请先运行 theme3_constituents_v2.py")
    return files[-1]


def safe_avg(values, default=0.0):
    vs = [v for v in values if v is not None and not np.isnan(v)]
    if not vs:
        return default
    return float(np.mean(vs))


def safe_pct(values, q=50):
    vs = [v for v in values if v is not None and not np.isnan(v)]
    if not vs:
        return 0.0
    return float(np.percentile(vs, q))


def state_label(flow_score, avg_change, leader_activity, spread_speed):
    """根据资金流强度、涨跌幅、龙头活跃度、扩散速度，判定 启动 / 发酵 / 主升 / 分歧 / 退潮"""
    # flow_score ~ 0-100, avg_change ~ -5~+10, leader_activity 0-100
    composite = (flow_score * 0.4
                 + avg_change * 3 * 0.25
                 + leader_activity * 0.2
                 + spread_speed * 0.15)

    if composite >= 70 and avg_change > 3 and leader_activity > 60:
        return "主升", composite
    if composite >= 50 and avg_change > 1:
        return "发酵", composite
    if composite >= 30 and avg_change > -1:
        return "启动", composite
    if composite >= 15:
        return "分歧", composite
    return "退潮", composite


# ============================================================
# Step 1: 聚合 一级主题 指标
# ============================================================

def build_macro_metrics(themes):
    """把 74 个二级主题聚合到 13 个一级主题，计算资金流综合指标
    注意：同一股票可能出现在多个二级主题，按 ts_code 去重后再计算聚合指标。
    """

    macro = defaultdict(lambda: {
        "sub_themes": [],
        "stocks_by_code": {},         # code → 该股票的 best 记录
        "n_stocks": 0,
        "total_amount": 0.0,
        "amount_share": 0.0,
        "avg_change_5d": 0.0,
        "avg_change_10d": 0.0,
        "n_leader": 0,
        "n_middle": 0,
        "n_lagger": 0,
        "n_limit_up": 0,
        "total_limit_up_days": 0,
        "leaders_with_board": [],
        "strong_laggers": [],
        "total_mv": 0.0,
        "avg_trend_score": 0.0,
        "spread_speed": 0.0,
    })

    for sub in themes:
        cat = sub["top_category"]
        m = macro[cat]
        m["sub_themes"].append(sub)

        for s in sub["stocks"]:
            code = s["ts_code"]
            # 按 combined_score 取该股票的最强记录（用于聚合去重）
            prev = m["stocks_by_code"].get(code)
            if prev is None or (s.get("combined_score", 0) or 0) > (prev.get("combined_score", 0) or 0):
                m["stocks_by_code"][code] = s

    # 现在基于去重后的 stocks_by_code 计算所有聚合指标
    for cat, m in macro.items():
        stocks = list(m["stocks_by_code"].values())
        m["n_stocks"] = len(stocks)

        changes_5d, changes_10d = [], []
        amount_list = []
        trend_scores = []
        for s in stocks:
            amt = s.get("avg_amount_5d", 0) or 0
            amount_list.append(amt)
            m["total_amount"] += amt
            changes_5d.append(s.get("change_5d_pct", 0) or 0)
            changes_10d.append(s.get("change_10d_pct", 0) or 0)
            trend_scores.append(s.get("trend_score", 0) or 0)
            mv = s.get("total_mv_wan", 0) or 0
            m["total_mv"] += mv
            lu = s.get("limit_up_days", 0) or 0
            if lu >= 1:
                m["n_limit_up"] += 1
                m["total_limit_up_days"] += lu

            role = s.get("role", "补涨")
            if role == "龙头":
                m["n_leader"] += 1
                if lu >= 1 or (s.get("change_5d_pct", 0) or 0) > 5:
                    m["leaders_with_board"].append({
                        "name": s["name"],
                        "code": code,
                        "limit_up_days": lu,
                        "change_5d": s.get("change_5d_pct", 0),
                        "trend_score": s.get("trend_score", 0),
                    })
            elif role == "中军":
                m["n_middle"] += 1
            else:
                m["n_lagger"] += 1
                if (s.get("change_5d_pct", 0) or 0) > 3:
                    m["strong_laggers"].append({
                        "name": s["name"],
                        "code": code,
                        "change_5d": s.get("change_5d_pct", 0),
                        "avg_amount": amt,
                    })

        m["avg_change_5d"] = safe_avg(changes_5d)
        m["avg_change_10d"] = safe_avg(changes_10d)
        m["avg_trend_score"] = safe_avg(trend_scores)

    # 市场总成交额（所有一级主题去重后合计 —— 注意跨一级主题也会重复
    # 一只股票可能同时在两个不同的一级主题（如 物理AI vs 半导体），这种情况下我们保留它在两边的出现，
    # 因为它确实是两个一级主题的交叉点和通道；这是真实的市场结构，不是噪音。
    total_market_amount = sum(v["total_amount"] for v in macro.values())

    for cat, m in macro.items():
        m["amount_share"] = m["total_amount"] / total_market_amount * 100 if total_market_amount > 0 else 0.0

        # 二级主题扩散速度 = 该一级主题下，有股票5日涨超3%的子主题数 / 总子主题数
        spread_count = sum(1 for sub in m["sub_themes"]
                           if any(s.get("change_5d_pct", 0) > 3 for s in sub["stocks"][:10]))
        m["spread_speed"] = spread_count / max(len(m["sub_themes"]), 1) * 100

        # 排序后去重（按 combined_score 已经保证唯一，这里再按 limit_up_days 排序即可）
        m["leaders_with_board"].sort(key=lambda x: (-x["limit_up_days"], -x["change_5d"]))
        m["strong_laggers"].sort(key=lambda x: -x["change_5d"])

    return macro


# ============================================================
# Step 2: 一级主题资金流综合评分（CapitalFlowScore）
# ============================================================

def compute_capital_flow_score(macro):
    """CapitalFlowScore =
       0.30 * 成交额占比(归一化)
     + 0.25 * 龙头强度(涨停/连板/新高)
     + 0.20 * 二级主题扩散速度
     + 0.15 * 补涨活跃度
     + 0.10 * 市场情绪强度
    """

    # 归一化因子
    max_amount_share = max((v["amount_share"] for v in macro.values()), default=1.0)
    max_spread = max((v["spread_speed"] for v in macro.values()), default=1.0)

    for cat, m in macro.items():
        n_stocks = max(m["n_stocks"], 1)

        # 1. 成交额占比 → 归一化到 0-100
        amount_score = (m["amount_share"] / max_amount_share) * 100 if max_amount_share > 0 else 0.0

        # 2. 龙头强度：连板数/涨停数/龙头5日涨幅 → 0-100
        leader_board_ratio = m["n_limit_up"] / n_stocks * 100
        avg_leader_change = safe_avg([x["change_5d"] for x in m["leaders_with_board"]], default=m["avg_change_5d"])
        leader_strength = min(100, leader_board_ratio * 8 + avg_leader_change * 3 + len(m["leaders_with_board"]) * 5)

        # 3. 二级主题扩散速度 → 0-100
        spread_score = m["spread_speed"] / max_spread * 100 if max_spread > 0 else 0.0

        # 4. 补涨活跃度 = strong_lagger 数 / 总补涨数 × 补涨平均涨幅
        if m["n_lagger"] > 0:
            lagger_ratio = len(m["strong_laggers"]) / m["n_lagger"] * 100
            lagger_avg_change = safe_avg([x["change_5d"] for x in m["strong_laggers"]], default=0)
            lagger_activity = min(100, lagger_ratio * 4 + lagger_avg_change * 5)
        else:
            lagger_activity = 0.0

        # 5. 市场情绪强度 = 趋势分均值 + 5日涨幅加权
        sentiment_score = min(100, m["avg_trend_score"] * 0.7 + m["avg_change_5d"] * 3)

        # 综合
        cfs = (0.30 * amount_score
               + 0.25 * leader_strength
               + 0.20 * spread_score
               + 0.15 * lagger_activity
               + 0.10 * sentiment_score)

        m["capital_flow_score"] = round(cfs, 2)
        m["amount_score"] = round(amount_score, 2)
        m["leader_strength"] = round(leader_strength, 2)
        m["spread_score"] = round(spread_score, 2)
        m["lagger_activity"] = round(lagger_activity, 2)
        m["sentiment_score"] = round(sentiment_score, 2)

        # 主题状态
        leader_activity = min(100, m["n_limit_up"] / n_stocks * 150)
        state, comp = state_label(cfs, m["avg_change_5d"], leader_activity, spread_score)
        m["state"] = state
        m["composite"] = round(comp, 2)


# ============================================================
# Step 3: 一级主题之间 共振 / 吸血 关系
# ============================================================

def build_macro_relationships(macro):
    """基于涨跌幅方向 + 成交额占比变化，推断共振/吸血"""
    cats = sorted(macro.keys(), key=lambda c: -macro[c]["capital_flow_score"])
    n = len(cats)

    relationships = {cat: {"synergy": [], "drain": [], "transition_to": []} for cat in cats}

    # 两两比较：涨跌幅同向 → 共振；资金占比一升一降 → 吸血；高分→低分扩散 → 传导
    for i in range(n):
        for j in range(i + 1, n):
            ca, cb = cats[i], cats[j]
            ma, mb = macro[ca], macro[cb]

            # 共振：两个主题都涨
            if ma["avg_change_5d"] > 0 and mb["avg_change_5d"] > 0:
                strength = round(min(ma["capital_flow_score"], mb["capital_flow_score"]) * 0.4, 2)
                if strength > 12:
                    relationships[ca]["synergy"].append({
                        "theme": cb, "strength": strength,
                        "note": f"双主题上涨 {ma['avg_change_5d']:+.1f}% / {mb['avg_change_5d']:+.1f}%"
                    })
                    relationships[cb]["synergy"].append({
                        "theme": ca, "strength": strength,
                        "note": f"双主题上涨 {mb['avg_change_5d']:+.1f}% / {ma['avg_change_5d']:+.1f}%"
                    })

            # 吸血：一个涨另一个跌 + 资金占比差异明显
            if (ma["avg_change_5d"] > 0 and mb["avg_change_5d"] < 0) or \
               (mb["avg_change_5d"] > 0 and ma["avg_change_5d"] < 0):
                winner = ca if ma["avg_change_5d"] > mb["avg_change_5d"] else cb
                loser = cb if winner == ca else ca
                strength = round(abs(macro[winner]["amount_share"] - macro[loser]["amount_share"]) * 3, 2)
                if strength > 2:
                    relationships[winner]["drain"].append({
                        "theme": loser, "strength": strength,
                        "note": f"资金从{loser}({macro[loser]['avg_change_5d']:+.1f}%)切换至{winner}({macro[winner]['avg_change_5d']:+.1f}%)"
                    })

            # 传导：高分主题 → 相邻主题（通过成交额/强度判断）
            if ma["capital_flow_score"] > 40 and mb["capital_flow_score"] > 25:
                # 如果两个主题 share 关键词或成分股 overlap，标记为传导
                overlap = set()
                for sa in ma["sub_themes"]:
                    for sb in mb["sub_themes"]:
                        acodes = {s["ts_code"] for s in sa["stocks"][:20]}
                        bcodes = {s["ts_code"] for s in sb["stocks"][:20]}
                        overlap.update(acodes & bcodes)
                        if len(overlap) > 3:
                            break
                    if len(overlap) > 3:
                        break
                if len(overlap) >= 2:
                    relationships[ca]["transition_to"].append({
                        "theme": cb, "overlap_stocks": len(overlap),
                        "strength": round(min(ma["capital_flow_score"], mb["capital_flow_score"]) * 0.4, 2)
                    })
                    relationships[cb]["transition_to"].append({
                        "theme": ca, "overlap_stocks": len(overlap),
                        "strength": round(min(ma["capital_flow_score"], mb["capital_flow_score"]) * 0.4, 2)
                    })

    return relationships


# ============================================================
# Step 4: 二级主题资金流
# ============================================================

def build_sub_theme_flow(themes, macro):
    """对每个二级主题计算 flow_score、角色分布、资金流向方向"""

    all_amount = sum(sum(s.get("avg_amount_5d", 0) or 0 for s in t["stocks"]) for t in themes)

    results = []
    for sub in themes:
        cat = sub["top_category"]
        n_stocks = sub["n_stocks"]
        if n_stocks == 0:
            continue

        leaders, middles, laggers = [], [], []
        for s in sub["stocks"]:
            entry = {
                "name": s["name"],
                "code": s["ts_code"],
                "change_5d": s.get("change_5d_pct", 0),
                "change_10d": s.get("change_10d_pct", 0),
                "avg_amount_5d": round((s.get("avg_amount_5d", 0) or 0) / 1e8, 2),  # 亿元
                "limit_up_days": s.get("limit_up_days", 0),
                "trend_score": s.get("trend_score", 0),
                "total_mv_yi": round((s.get("total_mv_wan", 0) or 0) / 1e4, 2),
            }
            role = s.get("role", "补涨")
            if role == "龙头":
                leaders.append(entry)
            elif role == "中军":
                middles.append(entry)
            else:
                laggers.append(entry)

        leaders.sort(key=lambda x: (-x["limit_up_days"], -x["change_5d"]))
        middles.sort(key=lambda x: -x["avg_amount_5d"])
        laggers.sort(key=lambda x: -x["change_5d"])

        # 计算指标
        changes5 = [s.get("change_5d_pct", 0) or 0 for s in sub["stocks"]]
        n_pos = sum(1 for v in changes5 if v > 0)
        n_neg = sum(1 for v in changes5 if v < 0)
        positive_ratio = n_pos / max(len(changes5), 1) * 100

        total_amount_sub = sum(s.get("avg_amount_5d", 0) or 0 for s in sub["stocks"])
        amount_share = total_amount_sub / all_amount * 100 if all_amount > 0 else 0.0

        n_limit_up = sum(1 for s in sub["stocks"] if (s.get("limit_up_days", 0) or 0) >= 1)

        # flow_score 简化版：40%成交额占比 + 30%涨跌比 + 30%涨停数强度
        avg_c5 = safe_avg(changes5)
        max_limit_up_pct = n_limit_up / n_stocks * 100
        flow_score = round(
            amount_share * 8    # 成交额权重
            + positive_ratio * 0.3
            + max_limit_up_pct * 1.5
            + max(0, avg_c5) * 2, 2)

        # flow_direction
        if avg_c5 > 2 and positive_ratio > 55 and n_limit_up >= 1:
            direction = "inflow"
        elif avg_c5 < -1 and positive_ratio < 40:
            direction = "outflow"
        else:
            direction = "rotation"

        # next_stage_prediction：根据补涨股数量与龙头状态推测
        if direction == "inflow" and len(laggers) > len(leaders) * 2:
            next_pred = "龙头稳定，补涨将扩散至低位股"
        elif direction == "inflow" and len(leaders) >= 2:
            next_pred = "龙头加速，资金进一步集中至核心股"
        elif direction == "rotation":
            next_pred = "主题内资金轮动，中军与补涨交替"
        else:
            next_pred = "资金流出，关注资金向强一级主题迁移"

        results.append({
            "macro_theme": cat,
            "sub_theme": sub["theme_name"],
            "theme_type": sub.get("theme_type", ""),
            "n_stocks": n_stocks,
            "flow_score": flow_score,
            "avg_change_5d": round(avg_c5, 2),
            "positive_ratio_pct": round(positive_ratio, 1),
            "n_limit_up": n_limit_up,
            "amount_share_pct": round(amount_share, 3),
            "role_distribution": {
                "leader": leaders[:8],
                "middle": middles[:8],
                "lagger": laggers[:8],
                "n_leader": len(leaders),
                "n_middle": len(middles),
                "n_lagger": len(laggers),
            },
            "flow_direction": direction,
            "next_stage_prediction": next_pred,
        })

    # 按 flow_score 降序
    results.sort(key=lambda r: -r["flow_score"])
    return results


# ============================================================
# Step 5: 资金流动路径
# ============================================================

def build_capital_flow_paths(macro, sub_flow, relationships):
    """路径：一级主题 → 二级主题 → 龙头/中军。过滤掉 outflow 的二级主题"""
    paths = []
    top_macros = sorted(macro.keys(), key=lambda c: -macro[c]["capital_flow_score"])[:5]

    for cat in top_macros:
        # 只取 inflow 或 rotation 的二级主题（按 flow_score 排序）
        subs_in_cat = [s for s in sub_flow
                        if s["macro_theme"] == cat and s["flow_direction"] in ("inflow", "rotation")]
        subs_in_cat.sort(key=lambda s: -s["flow_score"])
        if not subs_in_cat:
            continue

        for sub in subs_in_cat[:3]:
            leaders = sub["role_distribution"]["leader"][:2]
            middles = sub["role_distribution"]["middle"][:2]
            target_stocks = leaders + middles
            if not target_stocks:
                # 没有龙头/中军时，取 combined_score 最高的补涨
                if sub["role_distribution"]["lagger"][:1]:
                    target_stocks = sub["role_distribution"]["lagger"][:1]
                else:
                    continue

            top = target_stocks[0]
            path_str = f"{cat} → {sub['sub_theme']} → {top['name']}({top['code']})"
            strength = round(macro[cat]["capital_flow_score"] * 0.5 + sub["flow_score"] * 0.5, 2)

            if leaders:
                interp = f"{cat}主题中，'{sub['sub_theme']}'资金{sub['flow_direction']}，"
                interp += f"由 {leaders[0]['name']}({leaders[0]['limit_up_days']}连板) 领涨，"
                if middles:
                    interp += f"{middles[0]['name']}(5日均{middles[0]['avg_amount_5d']}亿)作为中军承接；"
                interp += f"{sub['next_stage_prediction']}"
            else:
                interp = f"{cat} → {sub['sub_theme']}路径由中军主导；{sub['next_stage_prediction']}"

            paths.append({
                "path": path_str,
                "macro_theme": cat,
                "sub_theme": sub["sub_theme"],
                "anchor_stock": f"{top['name']}({top['code']})",
                "anchor_role": "龙头" if any(l["code"] == top["code"] for l in (leaders or [])) else "中军",
                "flow_strength": strength,
                "avg_change_5d": sub["avg_change_5d"],
                "flow_direction": sub["flow_direction"],
                "interpretation": interp,
            })

    # 补充跨一级主题传导路径（吸血方向相反，即 loser → winner 的资金迁移）
    for cat in top_macros:
        for drain in relationships[cat]["drain"]:
            loser = drain["theme"]
            drain_strength = drain["strength"]
            # 构造路径：loser 最强二级 → cat 最强二级
            loser_sub = [s for s in sub_flow if s["macro_theme"] == loser]
            cat_sub = [s for s in sub_flow if s["macro_theme"] == cat]
            if not loser_sub or not cat_sub:
                continue
            ls = loser_sub[0]
            cs = cat_sub[0]
            path = f"{loser}(失血) → {cat}(吸金) | {ls['sub_theme']} → {cs['sub_theme']}"
            strength = round(macro[cat]["capital_flow_score"] * 0.6 + drain_strength * 0.4, 2)
            interp = f"资金从{loser}主题的{ls['sub_theme']}抽出(5日{ls['avg_change_5d']:+.1f}%)，"
            interp += f"切换至{cat}主题的{cs['sub_theme']}(5日{cs['avg_change_5d']:+.1f}%)；{cs['next_stage_prediction']}"
            paths.append({
                "path": path,
                "macro_theme": cat,
                "sub_theme": cs["sub_theme"],
                "anchor_stock": (
                    f"{cs['role_distribution']['leader'][0]['name']}"
                    if cs["role_distribution"]["leader"] else
                    f"{cs['role_distribution']['middle'][0]['name'] if cs['role_distribution']['middle'] else ''}"
                ),
                "anchor_role": "资金迁移目标",
                "flow_strength": strength,
                "avg_change_5d": cs["avg_change_5d"],
                "flow_direction": "inflow",
                "interpretation": interp,
            })

    paths.sort(key=lambda p: -p["flow_strength"])
    return paths


# ============================================================
# Step 6: 下一阶段热点预测
# ============================================================

def predict_next_hotspot(macro, sub_flow, relationships):
    """预测下一阶段热点：
    - 当前最强一级主题下，flow_score 次高且方向为 inflow/rotation 的二级主题
    - 跨一级主题：capital_flow_score 次高且 5日涨幅为正的一级主题
    """

    # 当前资金主战场
    sorted_macros = sorted(macro.keys(), key=lambda c: -macro[c]["capital_flow_score"])
    top_cat = sorted_macros[0]
    top_macro = macro[top_cat]

    # 在该一级主题下：优先 inflow 主题中 flow_score 次高的（第2个）作为新扩散方向
    subs_in_cat = [s for s in sub_flow
                    if s["macro_theme"] == top_cat and s["flow_direction"] in ("inflow", "rotation")]
    subs_in_cat.sort(key=lambda s: -s["flow_score"])

    # 如果只有1个 inflow 主题，再找 rotation 中最强的作为候选；否则取第2个
    candidate_sub = None
    if len(subs_in_cat) >= 2:
        candidate_sub = subs_in_cat[1]
    elif len(subs_in_cat) == 1:
        candidate_sub = subs_in_cat[0]

    # 跨一级主题：找 flow_score > 50 且 avg_change_5d >= 0 的其他一级主题
    cross_macros = [c for c in sorted_macros[1:]
                    if macro[c]["capital_flow_score"] > 50 and macro[c]["avg_change_5d"] >= -0.5]

    confidence = 0.0
    reason = ""

    if candidate_sub:
        # 置信度：flow_score / 80 + 方向加成
        dir_bonus = 0.15 if candidate_sub["flow_direction"] == "inflow" else 0.0
        confidence = round(min(0.95, min(candidate_sub["flow_score"] / 80, 0.7) + dir_bonus), 2)

        strongest_sub = subs_in_cat[0] if subs_in_cat else candidate_sub
        reason = (
            f"资金主战场在'{top_cat}'(CapitalFlowScore={top_macro['capital_flow_score']:.1f}，"
            f"{len(top_macro['leaders_with_board'])}只龙头，成交占比{top_macro['amount_share']:.2f}%)。"
            f"当前最强二级是'{strongest_sub['sub_theme']}'(涨停{strongest_sub['n_limit_up']}只)；"
            f"下一扩散方向预计指向'{candidate_sub['sub_theme']}'"
            f"(5日+{candidate_sub['avg_change_5d']:.1f}%，涨停{candidate_sub['n_limit_up']}只，"
            f"方向{candidate_sub['flow_direction']})。{candidate_sub['next_stage_prediction']}"
        )

        if cross_macros:
            cm = cross_macros[0]
            reason += (
                f" 跨一级主题关注'{cm}'(CapitalFlowScore={macro[cm]['capital_flow_score']:.1f}，"
                f"5日+{macro[cm]['avg_change_5d']:.1f}%)"
            )
    else:
        # 没有明显的子主题，取整体最强一级主题
        confidence = 0.4
        reason = f"主题图谱整体较散，维持关注'{top_cat}'为主战场"

    return {
        "macro_theme": top_cat,
        "sub_theme": candidate_sub["sub_theme"] if candidate_sub else "",
        "confidence": confidence,
        "reason": reason,
    }


# ============================================================
# Step 7: 组装输出 + 人类可读摘要
# ============================================================

def build_output(themes, trade_date):
    macro = build_macro_metrics(themes)
    compute_capital_flow_score(macro)
    relationships = build_macro_relationships(macro)
    sub_flow = build_sub_theme_flow(themes, macro)
    paths = build_capital_flow_paths(macro, sub_flow, relationships)
    next_hot = predict_next_hotspot(macro, sub_flow, relationships)

    # 输出一级主题图谱
    macro_graph = []
    for cat in sorted(macro.keys(), key=lambda c: -macro[c]["capital_flow_score"]):
        m = macro[cat]
        macro_graph.append({
            "theme": cat,
            "strength": round(m["avg_change_5d"], 2),
            "capital_flow_score": m["capital_flow_score"],
            "state": m["state"],
            "n_stocks": m["n_stocks"],
            "amount_share_pct": round(m["amount_share"], 3),
            "avg_change_5d": m["avg_change_5d"],
            "avg_change_10d": m["avg_change_10d"],
            "avg_trend_score": round(m["avg_trend_score"], 1),
            "n_leader": m["n_leader"],
            "n_middle": m["n_middle"],
            "n_lagger": m["n_lagger"],
            "n_limit_up": m["n_limit_up"],
            "top_leaders": m["leaders_with_board"][:5],
            "top_laggers": m["strong_laggers"][:5],
            "sub_theme_count": len(m["sub_themes"]),
            "sub_themes": [s["theme_name"] for s in m["sub_themes"]],
            "connected_themes": {
                "synergy": relationships[cat]["synergy"][:5],
                "drain": relationships[cat]["drain"][:5],
                "transition_to": relationships[cat]["transition_to"][:5],
            },
            "scores": {
                "amount_score": m["amount_score"],
                "leader_strength": m["leader_strength"],
                "spread_score": m["spread_score"],
                "lagger_activity": m["lagger_activity"],
                "sentiment_score": m["sentiment_score"],
            },
        })

    # 汇总输出
    result = {
        "trade_date": trade_date,
        "engine": "Theme Graph & Capital Flow Engine V3",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market_summary": {
            "total_macro_themes": len(macro_graph),
            "total_sub_themes": len(sub_flow),
            "total_stocks": sum(m["n_stocks"] for m in macro.values()),
            "top_3_macros_by_flow": [
                {"theme": m["theme"], "score": m["capital_flow_score"], "state": m["state"]}
                for m in macro_graph[:3]
            ],
        },
        "macro_theme_graph": macro_graph,
        "sub_theme_flow": sub_flow[:40],   # 只取前40个最强二级主题
        "capital_flow_paths": paths[:15],  # 只取前15条最关键路径
        "next_hotspot_prediction": next_hot,
    }

    return result


def print_human_summary(output):
    """在终端打印简洁的人类可读摘要"""
    print("\n" + "=" * 80)
    print(f" Theme Graph & Capital Flow Engine V3    | trade_date = {output['trade_date']}")
    print("=" * 80)

    # 三大核心问题
    print("\n[Q1] 当前资金在哪个一级主题？")
    for m in output["macro_theme_graph"][:5]:
        bar = "█" * int(min(m["capital_flow_score"], 100) / 5)
        print(f"   {m['theme']:<12} score={m['capital_flow_score']:>6.1f} {m['state']:<4}  "
              f"成交占比{m['amount_share_pct']:>5.2f}%  5日{m['avg_change_5d']:+5.1f}%  涨停{m['n_limit_up']}只")
        print(f"   {bar:<20}  龙头: {', '.join([l['name'] for l in m['top_leaders'][:3]]) or '-'}")

    print("\n[Q2] 正在向哪个二级主题扩散？（Top6 资金路径）")
    for p in output["capital_flow_paths"][:6]:
        print(f"   → {p['path']}")
        print(f"     强度={p['flow_strength']:.1f}  {p['interpretation']}")

    print("\n[Q3] 下一阶段资金可能切换到哪里？")
    nh = output["next_hotspot_prediction"]
    print(f"   预测: {nh['macro_theme']} → {nh['sub_theme']}  (confidence={nh['confidence']:.2f})")
    print(f"   理由: {nh['reason']}")

    # 共振/吸血关系
    print("\n[主题关系] 主要共振 & 吸血：")
    seen_pairs = set()
    for m in output["macro_theme_graph"][:5]:
        for s in m["connected_themes"]["synergy"][:2]:
            pair = tuple(sorted([m["theme"], s["theme"]]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            print(f"   [共振] {m['theme']} ↔ {s['theme']} (强度 {s['strength']:.1f})  {s['note']}")
        for d in m["connected_themes"]["drain"][:2]:
            print(f"   [吸血] {m['theme']} ← {d['theme']} (强度 {d['strength']:.1f})  {d['note']}")

    print("\n" + "=" * 80)


def main():
    json_path = find_latest_constituents_json()
    print(f"[Data] 读取成份股: {json_path}")

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    themes = data["themes"]
    trade_date = data.get("trade_date", "unknown")
    print(f"[Data] 共 {len(themes)} 个二级主题, trade_date={trade_date}")

    output = build_output(themes, trade_date)

    out_json = os.path.join(CACHE_DIR, f"theme_capital_flow_{trade_date}.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 已输出: {out_json}")

    print_human_summary(output)


if __name__ == "__main__":
    main()
