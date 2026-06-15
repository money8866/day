#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Lagging Breakout Selector V3.6
===============================
在已确定"主升二级主题 + 扩散路径"的前提下，输出：
  A. Next Day Breakout（次日首板/加速）
  B. Intraday Trigger（盘中即将启动）
  C. Capital Rotation Node（资金切换节点）

核心评分 BreakoutScore:
  0.30 × 资金异动强度（成交量/换手）
+ 0.25 × 产业链补涨位置（越末端越高）
+ 0.20 × 涨幅滞后度（相对板块）
+ 0.15 × 首次突破信号（首板/首阳）
+ 0.10 × 市值弹性（小市值优先）

四阶段识别: 未动 → 试探 → 确认 → 加速
"""
import json
import os
import sys
import glob
from collections import defaultdict
from datetime import datetime

import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")

MIN_SUB_STOCKS = 5       # 二级主题最少成分股数
TOP_MACRO_CANDIDATES = 3  # 同时分析前几名一级主题


# =====================================================================
# 工具函数
# =====================================================================

def safe_avg(values, default=0.0):
    vs = [v for v in values if v is not None]
    return float(np.mean(vs)) if vs else default


def get_or_zero(d, key):
    v = d.get(key, 0)
    return v if v is not None else 0


# =====================================================================
# Step 1: 识别主升一级主题与子主题结构
# =====================================================================

def analyze_macro_and_subs(themes):
    """
    对每个一级主题计算 aggregate 指标，并识别其下的二级主题强弱排名。
    """
    macro_map = defaultdict(list)
    for sub in themes:
        macro_map[sub["top_category"]].append(sub)

    macro_stats = []
    for cat, subs in macro_map.items():
        all_stocks_in_macro = []
        for s in subs:
            all_stocks_in_macro.extend(s["stocks"])

        n_total = len(all_stocks_in_macro)
        if n_total == 0:
            continue

        changes_5d = [stk.get("change_5d_pct", 0) or 0 for stk in all_stocks_in_macro]
        changes_10d = [stk.get("change_10d_pct", 0) or 0 for stk in all_stocks_in_macro]
        amounts = [stk.get("avg_amount_5d", 0) or 0 for stk in all_stocks_in_macro]
        limit_up_list = [stk for stk in all_stocks_in_macro if (stk.get("limit_up_days", 0) or 0) >= 1]
        recent_up_list = [stk for stk in all_stocks_in_macro if (stk.get("recent_up_days", 0) or 0) >= 1]

        leader_list = [stk for stk in all_stocks_in_macro if stk.get("role") == "龙头"]
        middle_list = [stk for stk in all_stocks_in_macro if stk.get("role") == "中军"]
        lagger_list = [stk for stk in all_stocks_in_macro if stk.get("role") not in ("龙头", "中军")]

        # 按 涨停数 × 平均涨幅 × 总成交额 归一化
        avg_chg5 = safe_avg(changes_5d)
        avg_chg10 = safe_avg(changes_10d)
        total_amount = sum(amounts)
        n_limit_up = len(limit_up_list)
        n_recent_up = len(recent_up_list)

        # 计算该一级主题的 strength score（综合）
        strength = (
            n_limit_up * 8
            + max(0, avg_chg5) * 5
            + total_amount / 1e8 * 0.2
        )

        # 主题阶段（简单推断）
        if avg_chg5 > 3 and n_limit_up >= 3:
            stage = "主升"
        elif avg_chg5 > 0 and n_limit_up >= 1:
            stage = "启动"
        elif avg_chg5 > -2:
            stage = "分歧"
        else:
            stage = "退潮"

        # 识别该一级主题下每个二级主题的 strength / 位置
        sub_list = []
        for sub in subs:
            sub_stocks = sub["stocks"]
            if len(sub_stocks) < MIN_SUB_STOCKS:
                continue
            sub_changes5 = [s.get("change_5d_pct", 0) or 0 for s in sub_stocks]
            sub_changes10 = [s.get("change_10d_pct", 0) or 0 for s in sub_stocks]
            sub_amount = sum(s.get("avg_amount_5d", 0) or 0 for s in sub_stocks)
            sub_limit_up = [s for s in sub_stocks if (s.get("limit_up_days", 0) or 0) >= 1]

            # 二级主题 strength = 涨停数 + 正涨幅贡献 + 成交额对数
            sub_strength = (
                len(sub_limit_up) * 10
                + max(0, safe_avg(sub_changes5)) * 4
                + sub_amount / 1e8 * 0.2
            )

            # 取该二级主题龙头（涨幅最高或首板）
            leaders = sorted(sub_stocks,
                             key=lambda s: (-(s.get("limit_up_days", 0) or 0),
                                            -(s.get("change_5d_pct", 0) or 0)))

            sub_list.append({
                "theme_name": sub["theme_name"],
                "n_stocks": len(sub_stocks),
                "avg_change_5d_pct": round(safe_avg(sub_changes5), 2),
                "avg_change_10d_pct": round(safe_avg(sub_changes10), 2),
                "total_amount": sub_amount,
                "n_limit_up": len(sub_limit_up),
                "strength": round(sub_strength, 2),
                "leaders": [{"code": l["ts_code"], "name": l["name"],
                             "limit_up_days": l.get("limit_up_days", 0),
                             "change_5d": l.get("change_5d_pct", 0),
                             "avg_amount_5d": l.get("avg_amount_5d", 0),
                             "total_mv_wan": l.get("total_mv_wan", 0)}
                           for l in leaders[:5]],
                "all_stocks": sub_stocks,
            })

        sub_list.sort(key=lambda s: -s["strength"])

        # 为每个二级主题分配扩散位置（core / mid_expansion / edge）
        total_subs = len(sub_list)
        for idx, sub in enumerate(sub_list):
            if idx == 0:
                sub["diffusion_position"] = "core_core"
            elif idx < max(1, int(total_subs * 0.3)):
                sub["diffusion_position"] = "core"
            elif idx < max(1, int(total_subs * 0.7)):
                sub["diffusion_position"] = "mid_expansion"
            else:
                sub["diffusion_position"] = "edge"

            sub["position_rank"] = idx + 1

        macro_stats.append({
            "macro_theme": cat,
            "n_stocks_total": n_total,
            "n_sub_themes": len(sub_list),
            "capital_flow_score": round(strength, 2),
            "avg_change_5d_pct": round(avg_chg5, 2),
            "avg_change_10d_pct": round(avg_chg10, 2),
            "total_amount": total_amount,
            "n_limit_up": n_limit_up,
            "n_recent_up_days": n_recent_up,
            "stage": stage,
            "sub_themes_sorted": sub_list,
            "top_leader_stocks": sorted(leader_list,
                                         key=lambda s: (-(s.get("limit_up_days", 0) or 0),
                                                        -(s.get("change_5d_pct", 0) or 0)))[:3],
            "top_middle_stocks": sorted(middle_list,
                                         key=lambda s: (-(s.get("combined_score", 0) or 0),
                                                        -(s.get("avg_amount_5d", 0) or 0)))[:5],
            "n_leaders": len(leader_list),
            "n_middles": len(middle_list),
            "n_laggers": len(lagger_list),
        })

    macro_stats.sort(key=lambda m: -m["capital_flow_score"])
    return macro_stats


# =====================================================================
# Step 2: BreakoutScore 计算
# =====================================================================

def compute_breakout_score(stock, sub_theme, macro_data):
    """
    BreakoutScore =
      0.30 × 资金异动强度（成交量/换手）
    + 0.25 × 产业链补涨位置（越末端越高）
    + 0.20 × 涨幅滞后度（相对板块）
    + 0.15 × 首次突破信号
    + 0.10 × 市值弹性
    归一化至 0-100
    """
    sub_avg_change5 = sub_theme["avg_change_5d_pct"]
    sub_avg_change10 = sub_theme["avg_change_10d_pct"]
    sub_top3_amount = safe_avg([
        (l["avg_amount_5d"] or 0) for l in sub_theme["leaders"][:3]
    ], default=0.0) if sub_theme["leaders"] else 0.0

    # ---- 1) 资金异动强度 (capital_signal, 0-100) ----
    avg_amount_5d = stock.get("avg_amount_5d", 0) or 0
    # 成交额是否显著放大（相对该二级主题龙头成交额的比例）
    if sub_top3_amount > 0:
        amount_ratio_score = min(100, (avg_amount_5d / sub_top3_amount) * 70)
    else:
        amount_ratio_score = 30

    # 最近涨停/首阳信号（资金首次介入）
    limit_up_days = stock.get("limit_up_days", 0) or 0
    recent_up_days = stock.get("recent_up_days", 0) or 0
    ma10_slope = stock.get("ma10_slope_pct", 0) or 0
    close_above_ma5 = stock.get("close_above_ma5", False)

    signal_score = 0
    if limit_up_days >= 1:
        signal_score += 40  # 已经涨停
    if recent_up_days >= 1 and limit_up_days < 2:
        signal_score += 20  # 首阳/放量试探
    if close_above_ma5:
        signal_score += 15
    if ma10_slope > 0:
        signal_score += 15
    elif ma10_slope > -1:
        signal_score += 5
    signal_score = min(100, signal_score)

    capital_signal = 0.6 * signal_score + 0.4 * amount_ratio_score

    # ---- 2) 产业链补涨位置 (position_score, 0-100) ----
    chain_dist = stock.get("chain_distance", 0) or 0
    # 子主题扩散位置 + chain_distance
    position_weight = {
        "core_core": 30, "core": 50, "mid_expansion": 70, "edge": 100
    }.get(sub_theme["diffusion_position"], 60)
    position_score = position_weight * 0.6 + chain_dist * 30
    position_score = min(100, position_score)

    # ---- 3) 涨幅滞后度 (lag_score, 0-100) ----
    stock_chg5 = stock.get("change_5d_pct", 0) or 0
    stock_chg10 = stock.get("change_10d_pct", 0) or 0
    # 相对板块平均滞后
    lag_5 = max(0, 1 - stock_chg5 / max(sub_avg_change5, 1)) * 100 if sub_avg_change5 > 0 \
        else min(100, max(0, (sub_avg_change5 - stock_chg5)) * 10)
    lag_10 = max(0, 1 - stock_chg10 / max(sub_avg_change10, 1)) * 100 if sub_avg_change10 > 0 \
        else min(100, max(0, (sub_avg_change10 - stock_chg10)) * 10)
    lag_score = min(100, (lag_5 + lag_10) / 2)

    # ---- 4) 首次突破信号 (first_breakout_signal, 0-100) ----
    fb_score = 0
    if limit_up_days == 1 and recent_up_days <= 2:
        fb_score += 60  # 首板，最理想
    elif limit_up_days >= 2:
        fb_score += 30  # 已连板，不是首次
    elif close_above_ma5 and ma10_slope > 0 and recent_up_days >= 1:
        fb_score += 70  # 首阳+站上MA5，即将突破
    elif close_above_ma5 and ma10_slope > 0:
        fb_score += 45
    elif close_above_ma5:
        fb_score += 25
    elif ma10_slope > 0:
        fb_score += 20
    # 如果5日涨幅为正但没涨停，奖励"温和启动"
    if 0 < stock_chg5 < 8 and limit_up_days == 0:
        fb_score += 15
    fb_score = min(100, fb_score)

    # ---- 5) 市值弹性 (size_score, 0-100) ----
    total_mv_wan = stock.get("total_mv_wan", 0) or 0
    if total_mv_wan <= 0:
        size_score = 40
    else:
        total_mv_yi = total_mv_wan / 10000  # 亿
        size_score = max(0, min(100, -40 * np.log10(max(total_mv_yi, 0.01)) + 150))

    # ---- 综合 BreakoutScore ----
    breakout_score = round(
        0.30 * capital_signal
        + 0.25 * position_score
        + 0.20 * lag_score
        + 0.15 * fb_score
        + 0.10 * size_score
        , 2
    )

    # ---- 四阶段识别 (未动 / 试探 / 确认 / 加速) ----
    if limit_up_days >= 2 and stock_chg5 > 15:
        stage = "加速"
    elif limit_up_days >= 1 or (close_above_ma5 and ma10_slope > 0 and stock_chg5 > 3):
        stage = "确认"
    elif recent_up_days >= 1 or ma10_slope > 0 or stock_chg5 > 0:
        stage = "试探"
    else:
        stage = "未动"

    # ---- 信号类型判断 ----
    if limit_up_days >= 1:
        trigger_type = "first_board"
    elif close_above_ma5 and ma10_slope > 0 and stock_chg5 > 2:
        trigger_type = "breakout"
    elif avg_amount_5d > sub_top3_amount * 0.3 and sub_top3_amount > 0:
        trigger_type = "volume_spike"
    elif recent_up_days >= 1:
        trigger_type = "volume_spike"
    else:
        trigger_type = "reversal" if stock_chg5 > 0 else "accumulation"

    return breakout_score, stage, trigger_type, {
        "capital_signal": round(capital_signal, 2),
        "position_score": round(position_score, 2),
        "lag_score": round(lag_score, 2),
        "first_breakout_signal": round(fb_score, 2),
        "size_score": round(size_score, 2),
        "sub_avg_change5": sub_avg_change5,
        "sub_top3_amount_yi": round(sub_top3_amount / 1e8, 2),
        "diffusion_position": sub_theme["diffusion_position"],
    }


# =====================================================================
# Step 3: 对每个主升一级主题生成补涨机会
# =====================================================================

def build_lagging_opportunities(macro_stats):
    """
    对 CapitalFlowScore Top N 的一级主题，分别分析：
      A. 次日首板/加速
      B. 盘中即将启动
      C. 资金切换节点
    并给出成功概率排序（best_lagging_candidates）。
    """
    results = []
    for macro in macro_stats[:TOP_MACRO_CANDIDATES]:
        sub_themes = macro["sub_themes_sorted"]
        if not sub_themes:
            continue

        # 获取该一级主题的所有潜在补涨股（去重）
        all_scored = {}
        for sub in sub_themes:
            for stock in sub["all_stocks"]:
                code = stock["ts_code"]
                if code in all_scored:
                    # 已存在，取更高分数版本
                    continue
                bs, stage, trigger, sub_scores = compute_breakout_score(stock, sub, macro)
                # 过滤 Rule1: 必须属于主升扩展链（core/中/末端都接受，
                #         但 edge/mid 权重更高；core_core 低权重）
                # Rule2: 涨幅低于板块均值
                is_lagging = (stock.get("change_5d_pct", 0) or 0) < sub["avg_change_5d_pct"] or \
                             (stock.get("limit_up_days", 0) or 0) == 0
                # Rule3: 必须有资金介入信号（stage != 未动）
                # Rule4: 产业链末端位置偏好
                edge_bonus = {"core_core": 0, "core": 5, "mid_expansion": 12, "edge": 18}\
                    .get(sub["diffusion_position"], 0)
                final_score = round(bs + edge_bonus, 2)
                all_scored[code] = {
                    "stock": stock,
                    "sub_theme": sub["theme_name"],
                    "diffusion_position": sub["diffusion_position"],
                    "position_rank": sub["position_rank"],
                    "breakout_score": final_score,
                    "stage": stage,
                    "trigger_type": trigger,
                    "sub_scores": sub_scores,
                    "is_lagging_vs_sub": is_lagging,
                }

        # 按 breakout_score 降序
        scored_list = sorted(all_scored.values(), key=lambda x: -x["breakout_score"])

        # ---- A类: Next Day Breakout（次日首板/加速）----
        # 必须：stage=确认 或 加速，且有资金介入信号
        next_day_breakouts = []
        for item in scored_list:
            if item["stage"] in ("确认", "加速") and item["trigger_type"] in ("first_board", "breakout"):
                if len(next_day_breakouts) >= 10:
                    break
                s = item["stock"]
                conf = round(min(0.95, item["breakout_score"] / 100), 2)
                reason = (
                    f"{s['name']}位于'{item['sub_theme']}'({item['diffusion_position']}) "
                    f"{item['trigger_type']}: 5日+{s.get('change_5d_pct',0):+.1f}%, "
                    f"连板{s.get('limit_up_days',0)}, 市值{(s.get('total_mv_wan',0) or 0)/1e4:.0f}亿, "
                    f"MA10+{s.get('ma10_slope_pct',0):+.1f}%, 站上MA5={'是' if s.get('close_above_ma5') else '否'}；"
                    f"相对板块涨幅滞后={item['sub_scores']['lag_score']:.0f}点, "
                    f"产业链末端={item['sub_scores']['position_score']:.0f}点"
                )
                expected_type = "first_board" if item["stage"] == "确认" and s.get("limit_up_days", 0) < 1 \
                    else "acceleration"
                next_day_breakouts.append({
                    "stock_code": s["ts_code"],
                    "stock_name": s["name"],
                    "breakout_score": item["breakout_score"],
                    "expected_type": expected_type,
                    "trigger_signal": item["trigger_type"],
                    "stage": item["stage"],
                    "reason": reason,
                    "confidence": conf,
                })

        # ---- B类: Intraday Trigger（盘中即将启动）----
        # stage=试探 或 未动但有 positive 信号
        intraday_triggers = []
        for item in scored_list:
            if item["stage"] in ("试探", "未动"):
                if len(intraday_triggers) >= 10:
                    break
                s = item["stock"]
                # 激活概率：试探>未动，MA10斜率>0奖励，且市值<100亿优先
                prob_base = {"未动": 0.35, "试探": 0.60}.get(item["stage"], 0.30)
                mv_yi = (s.get("total_mv_wan", 0) or 0) / 1e4
                prob = prob_base + (0.1 if 5 < mv_yi < 100 else 0) \
                    + (0.1 if s.get("ma10_slope_pct", 0) > 0 else 0) \
                    + (0.05 if s.get("close_above_ma5") else 0)
                prob = round(min(0.90, prob), 2)
                trigger_desc = {
                    "breakout": "首阳突破前高，MA10向上",
                    "volume_spike": "成交量放大，站上MA5",
                    "first_board": "首板资金介入",
                    "reversal": "由跌转涨，反转信号",
                    "accumulation": "低位温和放量，蓄力中",
                }.get(item["trigger_type"], "资金异动")

                reason = (
                    f"{s['name']}({item['sub_theme']}) {trigger_desc}；"
                    f"5日+{s.get('change_5d_pct',0):+.1f}%, "
                    f"MA10+{s.get('ma10_slope_pct',0):+.1f}%, "
                    f"市值{mv_yi:.0f}亿, 连板{s.get('limit_up_days',0)}, "
                    f"相对板块滞后{item['sub_scores']['lag_score']:.0f}点"
                )
                intraday_triggers.append({
                    "stock": f"{s['name']}({s['ts_code']})",
                    "stock_code": s["ts_code"],
                    "stock_name": s["name"],
                    "sub_theme": item["sub_theme"],
                    "trigger_type": item["trigger_type"],
                    "activation_probability": prob,
                    "stage": item["stage"],
                    "breakout_score": item["breakout_score"],
                    "reason": reason,
                })

        # ---- C类: Capital Rotation Node（资金切换节点）----
        # 龙头/中军 -> 补涨末端个股 路径
        rotation_nodes = []
        # 取该一级主题 top1 二级主题的龙头/中军作为 from 来源
        top_sub = sub_themes[0] if sub_themes else None
        top_leaders = top_sub["leaders"][:3] if top_sub else []
        top_middles_candidates = []
        for s2 in (top_sub["all_stocks"] if top_sub else []):
            if s2.get("role") == "中军":
                top_middles_candidates.append(s2)
        top_middles_candidates.sort(key=lambda s: (-(s.get("avg_amount_5d", 0) or 0)))
        top_middles = top_middles_candidates[:3]

        # 取 edge/mid 子主题中 breakout_score 最高的3只作为 to 目标
        edge_subs = [s for s in sub_themes if s["diffusion_position"] in ("mid_expansion", "edge")]
        edge_candidates = []
        seen_codes2 = set()
        for es in edge_subs:
            for stock in es["all_stocks"]:
                if stock["ts_code"] in seen_codes2:
                    continue
                seen_codes2.add(stock["ts_code"])
                bs, stage, tt, sub_sc = compute_breakout_score(stock, es, macro)
                edge_bonus = {"core_core": 0, "core": 5, "mid_expansion": 12, "edge": 18}\
                    .get(es["diffusion_position"], 0)
                final = round(bs + edge_bonus, 2)
                edge_candidates.append({
                    "stock": stock, "sub": es["theme_name"],
                    "score": final, "stage": stage, "trigger": tt,
                })
        edge_candidates.sort(key=lambda x: -x["score"])

        for from_stock in top_leaders + top_middles:
            from_name = from_stock.get("name", "") if isinstance(from_stock, dict) else ""
            from_code = from_stock.get("code", "") if isinstance(from_stock, dict) else from_stock.get("ts_code", "")
            from_amount = from_stock.get("avg_amount_5d", 0) if isinstance(from_stock, dict) \
                else from_stock.get("avg_amount_5d", 0)
            from_change = from_stock.get("change_5d", 0) if isinstance(from_stock, dict) \
                else from_stock.get("change_5d_pct", 0)
            if not from_name:
                continue
            for item in edge_candidates[:5]:
                ts = item["stock"]
                strength = round(item["score"] * 0.8 + max(0, (from_amount or 0) / 1e8 * 3), 2)
                interp = (
                    f"资金从 '{from_name}({from_code})'(5日+{from_change or 0:+.1f}%, "
                    f"资金中枢) 轮动至 '{ts['name']}({ts['ts_code']})'"
                    f"({item['sub']}, {item['stage']}, 5日+{ts.get('change_5d_pct',0):+.1f}%)；"
                    f"后者滞后期股，但资金已开始介入，是扩散路径末端候选"
                )
                rotation_nodes.append({
                    "from_stock": f"{from_name}({from_code})",
                    "to_stock": f"{ts['name']}({ts['ts_code']})",
                    "sub_theme": item["sub"],
                    "flow_direction": "inflow",
                    "strength": strength,
                    "interpretation": interp,
                })

        # 限制长度
        rotation_nodes = rotation_nodes[:10]

        # ---- D类: Best Lagging Candidates（成功概率排序）----
        best_lagging = []
        for item in scored_list:
            if len(best_lagging) >= 15:
                break
            s = item["stock"]
            mv_yi = (s.get("total_mv_wan", 0) or 0) / 1e4
            why_lagging = (
                f"5日+{s.get('change_5d_pct',0):+.1f}% vs 板块+{item['sub_scores']['sub_avg_change5']:.1f}%, "
                f"位置={item['diffusion_position']}, 链距={s.get('chain_distance',0)}, "
                f"市值{mv_yi:.0f}亿"
            )
            why_breakout_next = (
                f"{item['trigger_type']}信号: MA10+{s.get('ma10_slope_pct',0):+.1f}%, "
                f"站上MA5={'是' if s.get('close_above_ma5') else '否'}, "
                f"连板{s.get('limit_up_days',0)}, 近10日上涨{s.get('recent_up_days',0)}天"
            )
            best_lagging.append({
                "stock": f"{s['name']}({s['ts_code']})",
                "stock_code": s["ts_code"],
                "stock_name": s["name"],
                "sub_theme": item["sub_theme"],
                "lagging_score": item["breakout_score"],
                "stage": item["stage"],
                "trigger": item["trigger_type"],
                "why_lagging": why_lagging,
                "why_breakout_next": why_breakout_next,
                "confidence": round(min(0.95, item["breakout_score"] / 100), 2),
            })

        # ---- Next Rotation Signal：下一个热点子主题预测 ----
        # 找一个扩散排名 mid_expansion/edge 且 avg_change_5d > 0，n_limit_up 刚启动的
        next_sub_candidates = [
            s for s in sub_themes[1:]
            if s["diffusion_position"] in ("mid_expansion", "edge") and s["avg_change_5d_pct"] > -1
        ]
        if not next_sub_candidates:
            next_sub_candidates = sub_themes[1:4]

        if next_sub_candidates:
            next_sub = max(next_sub_candidates, key=lambda s: s["strength"])
            next_rotation_signal = {
                "sub_theme": next_sub["theme_name"],
                "direction": "inflow" if next_sub["avg_change_5d_pct"] > 0 else "rotation",
                "confidence": round(min(0.90, next_sub["strength"] / max(sub_themes[0]["strength"], 1)), 2),
                "reason": (
                    f"'{next_sub['theme_name']}'是'{macro['macro_theme']}'主题下排名#{next_sub['position_rank']}的"
                    f"{next_sub['diffusion_position']}子主题，5日+{next_sub['avg_change_5d_pct']:+.1f}%，"
                    f"涨停{next_sub['n_limit_up']}只，作为资金从核心轮动到扩散末端的候选"
                ),
            }
        else:
            next_rotation_signal = {
                "sub_theme": sub_themes[1]["theme_name"] if len(sub_themes) > 1 else sub_themes[0]["theme_name"],
                "direction": "rotation",
                "confidence": 0.4,
                "reason": "无明显扩散热点候选，维持核心主题关注",
            }

        results.append({
            "macro_theme": macro["macro_theme"],
            "capital_flow_score": macro["capital_flow_score"],
            "stage": macro["stage"],
            "avg_change_5d_pct": macro["avg_change_5d_pct"],
            "n_limit_up_total": macro["n_limit_up"],
            "top_leader_stocks": macro["top_leader_stocks"],
            "core_sub_theme": sub_themes[0]["theme_name"] if sub_themes else "",
            "next_day_breakouts": next_day_breakouts,
            "intraday_triggers": intraday_triggers,
            "capital_rotation_nodes": rotation_nodes,
            "best_lagging_candidates": best_lagging,
            "next_rotation_signal": next_rotation_signal,
        })

    return results


# =====================================================================
# Step 4: 组装输出 JSON + 人类可读摘要
# =====================================================================

def build_output(themes, trade_date):
    macro_stats = analyze_macro_and_subs(themes)
    opportunities = build_lagging_opportunities(macro_stats)

    # 汇总所有一级主题的 best_lagging_candidates，按 score 排序，输出全局"TOP 机会"
    all_combined = []
    for opt in opportunities:
        for item in opt["best_lagging_candidates"]:
            item2 = dict(item)
            item2["macro_theme"] = opt["macro_theme"]
            all_combined.append(item2)
    all_combined.sort(key=lambda x: -x["lagging_score"])

    output = {
        "trade_date": trade_date,
        "engine": "Lagging Breakout Selector V3.6",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "macro_theme_summary": [
            {
                "macro_theme": m["macro_theme"],
                "capital_flow_score": m["capital_flow_score"],
                "stage": m["stage"],
                "avg_change_5d_pct": m["avg_change_5d_pct"],
                "n_sub_themes": m["n_sub_themes"],
                "n_stocks_total": m["n_stocks_total"],
                "n_limit_up": m["n_limit_up"],
                "top_leaders": [
                    (f"{l.get('name','')}({l.get('code', l.get('ts_code', ''))})-"
                     f"{l.get('limit_up_days', 0)}连板")
                    for l in m["top_leader_stocks"]
                ],
            } for m in macro_stats[:5]
        ],
        "opportunities_by_macro": opportunities,
        "global_top_lagging_by_score": all_combined[:20],
    }
    return output


def print_human_summary(output):
    print("\n" + "=" * 90)
    print(f"  Lagging Breakout Selector V3.6   trade_date = {output['trade_date']}")
    print("=" * 90)

    # 总览
    print("\n[一级主题总览]")
    for m in output["macro_theme_summary"]:
        print(f"   {m['macro_theme']:<10} score={m['capital_flow_score']:>6.1f} "
              f"[{m['stage']:<3}] 5日+{m['avg_change_5d_pct']:>5.1f}% "
              f"涨停{m['n_limit_up']:>2}只 龙头={', '.join(m['top_leaders'][:3])}")

    # 按一级主题输出机会
    for opt in output["opportunities_by_macro"]:
        print(f"\n{'─' * 90}")
        print(f" 【{opt['macro_theme']}】 CapitalFlowScore={opt['capital_flow_score']:.1f} "
              f"[{opt['stage']}] 核心主题: {opt['core_sub_theme']}")

        # A类
        print(f"\n  [A] Next Day Breakout（次日首板/加速）→ Top {len(opt['next_day_breakouts'])}")
        if opt["next_day_breakouts"]:
            for i, s in enumerate(opt["next_day_breakouts"], 1):
                print(f"     {i:>2}. ★★★ {s['stock_name']:<10}({s['stock_code']}) "
                      f"score={s['breakout_score']:>5.1f} conf={s['confidence']:.2f} "
                      f"[{s['expected_type']}/{s['trigger_signal']}] [{s['stage']}]")
                print(f"          {s['reason']}")
        else:
            print("      （当前该一级主题下没有确认/加速信号的个股，请关注B类盘中触发）")

        # B类
        print(f"\n  [B] Intraday Trigger（盘中即将启动）→ Top {len(opt['intraday_triggers'])}")
        if opt["intraday_triggers"]:
            for i, s in enumerate(opt["intraday_triggers"], 1):
                print(f"     {i:>2}. ★★  {s['stock_name']:<10}({s['stock_code']}) "
                      f"激活概率={s['activation_probability']:.2f} [{s['trigger_type']}] [{s['stage']}]")
                print(f"          {s['reason']}")
        else:
            print("      （没有明确的盘中触发候选）")

        # C类
        print(f"\n  [C] Capital Rotation Node（资金切换节点）→ Top {len(opt['capital_rotation_nodes'])}")
        if opt["capital_rotation_nodes"]:
            for i, s in enumerate(opt["capital_rotation_nodes"][:5], 1):
                print(f"     {i:>2}. {s['from_stock']} → {s['to_stock']}")
                print(f"         强度={s['strength']:.1f} [{s['flow_direction']}]")
                print(f"         {s['interpretation']}")

        # 补涨排名
        print(f"\n  [补涨概率总榜] best_lagging_candidates Top 8:")
        for i, s in enumerate(opt["best_lagging_candidates"][:8], 1):
            print(f"     {i:>2}. {s['stock_name']:<10}({s['stock_code']}) "
                  f"score={s['lagging_score']:>5.1f} conf={s['confidence']:.2f} "
                  f"[{s['stage']}] [{s['trigger']}]")
            print(f"          为什么是补涨: {s['why_lagging']}")
            print(f"          为什么接下来可能爆发: {s['why_breakout_next']}")

        # 下一轮热点预测
        nr = opt["next_rotation_signal"]
        print(f"\n  [Next Rotation Signal] 下一轮资金切换热点预测:")
        print(f"     → {nr['sub_theme']} [{nr['direction']}] conf={nr['confidence']:.2f}")
        print(f"     {nr['reason']}")

    # 全局榜单
    print(f"\n{'='*90}")
    print(f"[GLOBAL TOP 10 补涨榜] 按 BreakoutScore 排序")
    for i, s in enumerate(output["global_top_lagging_by_score"][:10], 1):
        print(f"  {i:>2}. {s['stock_name']:<10}({s['stock_code']}) [{s['macro_theme']}] "
              f"score={s['lagging_score']:>5.1f} conf={s['confidence']:.2f} "
              f"[{s['stage']}/{s['trigger']}]")
    print("=" * 90)


def main():
    pattern = os.path.join(CACHE_DIR, "theme3_constituents_*.json")
    files = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError("未找到 theme3_constituents_*.json，请先运行 theme3_constituents_v2.py")
    json_path = files[-1]
    print(f"[Data] 读取: {json_path}")

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    themes = data["themes"]
    trade_date = data.get("trade_date", "unknown")
    print(f"[Data] {len(themes)} 个二级主题 / trade_date={trade_date}")

    output = build_output(themes, trade_date)

    out_json = os.path.join(CACHE_DIR, f"theme_breakout_selector_{trade_date}.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"✅ 已输出 JSON: {out_json}")

    print_human_summary(output)


if __name__ == "__main__":
    main()
