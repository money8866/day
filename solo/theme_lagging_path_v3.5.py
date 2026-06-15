#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Lagging Path & Second Wave Engine V3.5
=======================================
目标：不是选股，而是预测：
   1. 资金从当前主线龙头如何扩散
   2. 扩散到哪些二级主题
   3. 哪些个股将成为"最后一公里补涨爆发点"
   4. 哪些补涨点具备次日溢价最大概率

核心模型 LaggingPathScore:
   0.30 × 产业链扩散距离(chain_distance)
   0.25 × 涨幅滞后度(相对龙头差距)
   0.20 × 首次资金介入强度(首板/放量)
   0.15 × 主题扩散边缘性
   0.10 × 市值弹性(小市值优先)
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


def safe_avg(values, default=0.0):
    vs = [v for v in values if v is not None]
    return float(np.mean(vs)) if vs else default


def find_latest_constituents():
    files = sorted(glob.glob(os.path.join(CACHE_DIR, "theme3_constituents_*.json")))
    if not files:
        raise FileNotFoundError("未找到 theme3_constituents_*.json")
    return files[-1]


# =====================================================================
# Step 1: 资金主路径 (一级主题 → 核心二级主题 → 龙头锚)
# =====================================================================

def identify_main_flow(themes):
    macro_stats = {}
    for sub in themes:
        cat = sub["top_category"]
        if cat not in macro_stats:
            macro_stats[cat] = {
                "sub_themes": [],
                "total_amount": 0.0,
                "total_limit_up": 0,
                "sum_change_5d": 0.0,
                "count_stocks": 0,
                "leader_pool": [],
            }
        s = macro_stats[cat]
        s["sub_themes"].append(sub)

        for stk in sub["stocks"]:
            amt = stk.get("avg_amount_5d", 0) or 0
            s["total_amount"] += amt
            s["sum_change_5d"] += stk.get("change_5d_pct", 0) or 0
            s["count_stocks"] += 1
            if (stk.get("limit_up_days", 0) or 0) >= 1:
                s["total_limit_up"] += 1
            if (stk.get("limit_up_days", 0) or 0) >= 2 or (
                stk.get("role") == "龙头" and (stk.get("change_5d_pct", 0) or 0) > 5
            ):
                s["leader_pool"].append({
                    "code": stk["ts_code"],
                    "name": stk["name"],
                    "limit_up_days": stk.get("limit_up_days", 0),
                    "change_5d": stk.get("change_5d_pct", 0),
                    "sub_theme": sub["theme_name"],
                })

        s["avg_change_5d"] = s["sum_change_5d"] / max(s["count_stocks"], 1)

    max_amount = max((v["total_amount"] for v in macro_stats.values()), default=1.0)
    for cat, s in macro_stats.items():
        limit_score = min(100, s["total_limit_up"] * 6)
        chg_score = min(100, max(0, s["avg_change_5d"]) * 20 + 30)
        amount_score = s["total_amount"] / max_amount * 100
        s["capital_flow_score"] = round(0.4 * limit_score + 0.3 * chg_score + 0.3 * amount_score, 2)
        s["amount_share_pct"] = round(s["total_amount"] / max(
            sum(x["total_amount"] for x in macro_stats.values()), 1.0) * 100, 2)
        s["leader_pool"].sort(key=lambda x: (-x["limit_up_days"], -x["change_5d"]))

    # 选出最强一级主题
    sorted_macros = sorted(macro_stats.keys(), key=lambda c: -macro_stats[c]["capital_flow_score"])
    top_macro = sorted_macros[0]
    top_stats = macro_stats[top_macro]

    # 选出核心二级主题（该一级主题下最强的）
    sub_ranked = []
    for sub in top_stats["sub_themes"]:
        n_limit = sum(1 for stk in sub["stocks"] if (stk.get("limit_up_days", 0) or 0) >= 1)
        avg_chg5 = safe_avg([stk.get("change_5d_pct", 0) or 0 for stk in sub["stocks"]])
        total_amt = sum(stk.get("avg_amount_5d", 0) or 0 for stk in sub["stocks"])
        flow_score = n_limit * 8 + max(0, avg_chg5) * 5 + total_amt / 1e8 * 0.3
        sub_ranked.append({
            "sub": sub,
            "flow_score": round(flow_score, 2),
            "n_limit": n_limit,
            "avg_chg5": round(avg_chg5, 2),
            "total_amt": total_amt,
        })

    sub_ranked.sort(key=lambda x: -x["flow_score"])

    core_sub_entry = sub_ranked[0]
    core_sub = core_sub_entry["sub"]

    # 选核心龙头锚点
    leaders_sorted = sorted(core_sub["stocks"],
                            key=lambda stk: (-(stk.get("limit_up_days", 0) or 0),
                                             -(stk.get("combined_score", 0) or 0)))
    leader_anchor = leaders_sorted[0] if leaders_sorted else None

    return {
        "macro_theme": top_macro,
        "capital_flow_score": top_stats["capital_flow_score"],
        "amount_share_pct": top_stats["amount_share_pct"],
        "avg_change_5d_pct": round(top_stats["avg_change_5d"], 2),
        "total_limit_up": top_stats["total_limit_up"],
        "n_sub_themes": len(top_stats["sub_themes"]),
        "core_sub_theme": core_sub["theme_name"],
        "core_sub_avg_change_5d_pct": core_sub_entry["avg_chg5"],
        "leader_stock": f"{leader_anchor['name']}({leader_anchor['ts_code']})" if leader_anchor else "",
        "leader_limit_up_days": leader_anchor.get("limit_up_days", 0) if leader_anchor else 0,
        "leader_change_5d_pct": round(leader_anchor.get("change_5d_pct", 0) or 0, 2) if leader_anchor else 0,
        "top_3_leaders": [f"{l['name']}({l['code']})-{l['limit_up_days']}连板"
                          for l in top_stats["leader_pool"][:3]],
        "_macro_stats": macro_stats,
        "_sub_ranked": sub_ranked,
    }


# =====================================================================
# Step 2: 扩散路径 (核心二级主题 → 其他二级主题)
# =====================================================================

def build_expansion_paths(main_flow):
    macro_stats = main_flow["_macro_stats"]
    top_macro = main_flow["macro_theme"]
    core_sub_name = main_flow["core_sub_theme"]
    core_avg_chg = main_flow["core_sub_avg_change_5d_pct"]
    top_stats = macro_stats.get(top_macro, {"avg_change_5d": 0.0, "capital_flow_score": 0.0})

    sub_ranked = main_flow["_sub_ranked"]

    expansion = []
    for rank, entry in enumerate(sub_ranked):
        sn = entry["sub"]["theme_name"]
        if sn == core_sub_name:
            continue

        marginality = max(0, (sub_ranked[0]["flow_score"] - entry["flow_score"]) /
                          max(sub_ranked[0]["flow_score"], 1) * 100)

        if entry["avg_chg5"] > max(core_avg_chg * 0.5, 0) and entry["avg_chg5"] > 0:
            direction = "inflow"
        elif entry["avg_chg5"] < -2:
            direction = "outflow"
        else:
            direction = "rotation"

        strength = round(max(0, entry["flow_score"] - rank * 5), 2)

        expansion.append({
            "from_sub_theme": core_sub_name,
            "to_sub_theme": sn,
            "flow_direction": direction,
            "strength": strength,
            "diffusion_rank": rank,
            "marginality_pct": round(marginality, 1),
            "interpretation":
                f"资金从'{core_sub_name}'(5日+{core_avg_chg:.1f}%)"
                f"扩散至'{sn}'(5日+{entry['avg_chg5']:.1f}%, "
                f"涨停{entry['n_limit']}只)；方向={direction}；边缘性={marginality:.0f}%",
            "avg_change_5d_pct": entry["avg_chg5"],
            "n_limit_up": entry["n_limit"],
        })

    # 跨一级主题
    cross = []
    for other_cat in sorted(macro_stats.keys(),
                              key=lambda c: -macro_stats[c]["capital_flow_score"]):
        if other_cat == top_macro:
            continue
        if len(cross) >= 5:
            break
        os = macro_stats[other_cat]
        if os["avg_change_5d"] > 0 and os["total_limit_up"] >= 5:
            relation = "共振"
        elif os["avg_change_5d"] < 0 and top_stats.get("avg_change_5d", 0) > 0:
            relation = "失血_to"
        else:
            relation = "并行"
        cross.append({
            "macro_theme": other_cat,
            "capital_flow_score": os["capital_flow_score"],
            "avg_change_5d_pct": round(os["avg_change_5d"], 2),
            "n_limit_up": os["total_limit_up"],
            "relation_to_main": relation,
        })

    expansion.append({
        "cross_macro_themes": cross,
        "note": "跨一级主题扩散/吸血识别",
    })

    return expansion


# =====================================================================
# Step 3: 补涨评分 (LaggingPathScore)
# =====================================================================

def compute_lagging_path_score(stock, sub_avg_chg, leader_chg, diffusion_rank, is_edge_theme):
    chain_dist = stock.get("chain_distance", 0) or 0
    distance_score = min(100, chain_dist * 33)

    stock_chg5 = stock.get("change_5d_pct", 0) or 0
    if leader_chg > 0:
        lag_pct = max(0, (1 - stock_chg5 / max(leader_chg, 1)) * 100)
    else:
        lag_pct = 50.0

    limit_up_days = stock.get("limit_up_days", 0) or 0
    recent_up_days = stock.get("recent_up_days", 0) or 0
    close_above_ma5 = stock.get("close_above_ma5", False)
    ma10_slope = stock.get("ma10_slope_pct", 0) or 0

    first_fund = 0
    if limit_up_days >= 1 and recent_up_days <= 2:
        first_fund += 40
    elif limit_up_days >= 2:
        first_fund += 60
    elif recent_up_days >= 1:
        first_fund += 20
    if close_above_ma5:
        first_fund += 20
    if ma10_slope > 0:
        first_fund += 20
    elif ma10_slope > -1:
        first_fund += 10
    first_fund = min(100, first_fund)

    edge_score = 100 if is_edge_theme else 50
    edge_score = min(100, edge_score * 0.7 + diffusion_rank * 10)

    total_mv_wan = stock.get("total_mv_wan", 0) or 0
    if total_mv_wan <= 0:
        mcap_score = 50
    else:
        total_mv_yi = total_mv_wan / 10000
        mcap_score = max(0, min(100, -30 * np.log10(max(total_mv_yi, 0.1)) + 130))

    lagging_score = (
        0.30 * distance_score
        + 0.25 * lag_pct
        + 0.20 * first_fund
        + 0.15 * edge_score
        + 0.10 * mcap_score
    )

    if limit_up_days >= 1 and recent_up_days <= 2:
        trigger = "first_board"
    elif close_above_ma5 and ma10_slope > 0:
        trigger = "breakout"
    elif recent_up_days >= 1:
        trigger = "volume_spike"
    else:
        trigger = "accumulation"

    if stock_chg5 <= 0 and lag_pct > 60:
        stage = "early"
    elif lag_pct > 30:
        stage = "mid"
    else:
        stage = "late"

    return round(lagging_score, 2), stage, trigger, {
        "distance": round(distance_score, 1),
        "lag_pct": round(lag_pct, 1),
        "first_fund_signal": round(first_fund, 1),
        "edge_theme": round(edge_score, 1),
        "mcap_score": round(mcap_score, 1),
    }


# =====================================================================
# Step 4: 补涨路径节点 (Path 3)
# =====================================================================

def build_lagging_path_nodes(main_flow):
    top_macro = main_flow["macro_theme"]
    core_sub_name = main_flow["core_sub_theme"]
    macro_stats = main_flow["_macro_stats"]
    leader_chg = main_flow.get("leader_change_5d_pct", 5.0)
    sub_ranked = main_flow["_sub_ranked"]
    total_subs = len(sub_ranked)

    lagging_nodes = []
    for rank, entry in enumerate(sub_ranked):
        sub_name = entry["sub"]["theme_name"]
        if sub_name == core_sub_name:
            theme_class = "核心锚"
        elif rank < max(1, int(total_subs * 0.3)):
            theme_class = "扩散核心"
        elif rank < int(total_subs * 0.7):
            theme_class = "扩散中"
        else:
            theme_class = "边缘末端"

        avg_chg5 = entry["avg_chg5"]
        n_limit = entry["n_limit"]

        stocks_lagging = []
        for stk in entry["sub"]["stocks"]:
            lp_score, stage, trigger, sub_sc = compute_lagging_path_score(
                stk, avg_chg5, leader_chg, rank, is_edge_theme=(theme_class == "边缘末端")
            )
            is_undervalued = (stk.get("change_5d_pct", 0) or 0) < avg_chg5 or (stk.get("limit_up_days", 0) or 0) < 2

            stocks_lagging.append({
                "stock_code": stk["ts_code"],
                "stock_name": stk["name"],
                "lagging_path_score": lp_score,
                "stage": stage,
                "trigger": trigger,
                "sub_scores": sub_sc,
                "change_5d_pct": round(stk.get("change_5d_pct", 0), 2),
                "change_10d_pct": round(stk.get("change_10d_pct", 0), 2),
                "limit_up_days": stk.get("limit_up_days", 0),
                "recent_up_days": stk.get("recent_up_days", 0),
                "avg_amount_5d_yi": round((stk.get("avg_amount_5d", 0) or 0) / 1e8, 2),
                "total_mv_yi": round((stk.get("total_mv_wan", 0) or 0) / 1e4, 2),
                "ma10_slope_pct": round(stk.get("ma10_slope_pct", 0), 2),
                "trend_score": stk.get("trend_score", 0),
                "close_above_ma5": stk.get("close_above_ma5", False),
                "is_undervalued": is_undervalued,
                "chain_distance": stk.get("chain_distance", 0),
            })

        stocks_lagging.sort(key=lambda s: -s["lagging_path_score"])

        lagging_nodes.append({
            "sub_theme": sub_name,
            "theme_class": theme_class,
            "theme_flow_rank": rank + 1,
            "n_limit_up": n_limit,
            "avg_change_5d_pct": round(avg_chg5, 2),
            "is_edge_theme": (theme_class == "边缘末端"),
            "top_lagging_stocks": stocks_lagging[:5],
        })

    return lagging_nodes


# =====================================================================
# Step 5: 最后一公里补涨爆发点 + 下一阶段热点预测
# =====================================================================

def pick_final_burst_points(lagging_nodes, main_flow, top_n=10):
    all_stocks = []
    for theme in lagging_nodes:
        for stk in theme["top_lagging_stocks"]:
            all_stocks.append({
                **stk,
                "_sub_theme": theme["sub_theme"],
                "_theme_class": theme["theme_class"],
            })

    all_stocks.sort(key=lambda s: -s["lagging_path_score"])

    final_burst = []
    seen_codes = set()
    for stk in all_stocks:
        if stk["stock_code"] in seen_codes:
            continue
        seen_codes.add(stk["stock_code"])
        if len(final_burst) >= top_n:
            break
        score = stk["lagging_path_score"]
        trigger = stk["trigger"]

        if score >= 65 and trigger in ("first_board", "breakout"):
            premium = "high"
        elif score >= 50 or trigger in ("first_board", "volume_spike"):
            premium = "medium"
        else:
            premium = "low"

        confidence = round(min(0.95, score / 100), 2)

        reason = (
            f"{stk['stock_name']}位于'{stk['_sub_theme']}'({stk['_theme_class']}) "
            f"链距={stk['chain_distance']}, 5日+{stk['change_5d_pct']:.1f}%, "
            f"首板信号={stk['limit_up_days']}, 市值{stk['total_mv_yi']:.0f}亿, "
            f"MA10+{stk['ma10_slope_pct']:.1f}%, 触发={trigger}"
        )

        final_burst.append({
            "stock": f"{stk['stock_name']}({stk['stock_code']})",
            "stock_code": stk["stock_code"],
            "stock_name": stk["stock_name"],
            "sub_theme": stk["_sub_theme"],
            "theme_class": stk["_theme_class"],
            "lagging_path_score": score,
            "stage": stk["stage"],
            "trigger": trigger,
            "expected_next_day_return_bias": premium,
            "confidence": confidence,
            "reason": reason,
        })

    return final_burst


def predict_next_wave(lagging_nodes, main_flow):
    edge_themes = [t for t in lagging_nodes if t["is_edge_theme"]]
    if edge_themes:
        best = max(edge_themes,
                    key=lambda t: safe_avg([s["lagging_path_score"] for s in t["top_lagging_stocks"]]))
    else:
        if lagging_nodes:
            best = max(lagging_nodes,
                        key=lambda t: safe_avg([s["lagging_path_score"] for s in t["top_lagging_stocks"]]))
        else:
            return {"sub_theme": "", "direction": "rotation", "confidence": 0.0,
                    "reason": "数据不足"}

    avg_score = safe_avg([s["lagging_path_score"] for s in best["top_lagging_stocks"]])
    confidence = round(min(0.9, avg_score / 70), 2)
    reason = (
        f"'{best['sub_theme']}'是'{main_flow['macro_theme']}'主题的"
        f"{best['theme_class']}末端(涨停{best['n_limit_up']}只, 5日+{best['avg_change_5d_pct']:.1f}%), "
        f"top补涨股平均lagging_path_score={avg_score:.0f}, 是下一波资金扩散的首选目标"
    )

    return {
        "sub_theme": best["sub_theme"],
        "direction": "inflow",
        "confidence": confidence,
        "reason": reason,
    }


# =====================================================================
# Step 6: 组装输出 + 人类可读摘要
# =====================================================================

def build_output(themes, trade_date):
    main_flow = identify_main_flow(themes)
    expansion_paths = build_expansion_paths(main_flow)
    lagging_nodes = build_lagging_path_nodes(main_flow)
    final_burst = pick_final_burst_points(lagging_nodes, main_flow)
    next_wave = predict_next_wave(lagging_nodes, main_flow)

    output = {
        "trade_date": trade_date,
        "engine": "Lagging Path & Second Wave Engine V3.5",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),

        "main_flow": {
            "macro_theme": main_flow["macro_theme"],
            "core_sub_theme": main_flow["core_sub_theme"],
            "leader_stock": main_flow["leader_stock"],
            "leader_limit_up_days": main_flow["leader_limit_up_days"],
            "leader_change_5d_pct": main_flow["leader_change_5d_pct"],
            "capital_flow_score": main_flow["capital_flow_score"],
            "amount_share_pct": main_flow["amount_share_pct"],
            "avg_change_5d_pct": main_flow["avg_change_5d_pct"],
            "total_limit_up_stocks": main_flow["total_limit_up"],
            "n_sub_themes": main_flow["n_sub_themes"],
            "top_3_leaders_in_macro": main_flow["top_3_leaders"],
        },

        "expansion_paths": expansion_paths,
        "lagging_path_nodes": lagging_nodes,
        "final_burst_points": final_burst,
        "next_wave_prediction": next_wave,
    }

    return output


def print_human_summary(output):
    mt = output["main_flow"]
    print(f"\n{'='*90}")
    print(f"  Lagging Path & Second Wave Engine V3.5   trade_date = {output['trade_date']}")
    print(f"{'='*90}")

    print(f"\n[Path 1] 资金主路径: {mt['macro_theme']} → {mt['core_sub_theme']} → {mt['leader_stock']}")
    print(f"   CapitalFlowScore={mt['capital_flow_score']:.1f}, 成交占比={mt['amount_share_pct']:.2f}%, "
          f"主题内涨停={mt['total_limit_up_stocks']}只")
    print(f"   Top3龙头: {' / '.join(mt['top_3_leaders_in_macro'])}")

    print(f"\n[Path 2] 扩散路径 (Top6):")
    for i, p in enumerate(output["expansion_paths"]):
        if "to_sub_theme" not in p:
            if "cross_macro_themes" in p:
                print(f"\n   [跨一级主题] {p.get('note', '')}:")
                for c in p["cross_macro_themes"]:
                    print(f"     - {c['macro_theme']} flow={c['capital_flow_score']:.1f} "
                          f"5日+{c['avg_change_5d_pct']:.1f}% 涨停{c['n_limit_up']}只 [{c['relation_to_main']}]")
            continue
        if i >= 8:
            break
        icon = "↑" if p["flow_direction"] == "inflow" else ("↺" if p["flow_direction"] == "rotation" else "↓")
        print(f"   {icon} {p['to_sub_theme']:<20s} 强度{p['strength']:>5.1f} "
              f"5日+{p['avg_change_5d_pct']:>5.1f}% 涨停{p['n_limit_up']:>2}只 [{p['flow_direction']}]")

    print(f"\n[Path 3] 补涨路径节点 (主题分类 + Top3补涨股):")
    for t in output["lagging_path_nodes"]:
        label = "【边缘末端】" if t["is_edge_theme"] else ("【核心】" if t["theme_class"] == "核心锚" else "【扩散】")
        print(f"\n   {label} {t['sub_theme']:<18s} 排名#{t['theme_flow_rank']:<2d} "
              f"涨停{t['n_limit_up']:>2}只 5日+{t['avg_change_5d_pct']:>5.1f}%")
        for s in t["top_lagging_stocks"][:3]:
            stage_icon = {"early": "●early", "mid": "●mid  ", "late": "●late "}.get(s["stage"], s["stage"])
            print(f"       score={s['lagging_path_score']:>5.1f} {stage_icon} "
                  f"{s['stock_name']:<10s}({s['stock_code']}) "
                  f"5日+{s['change_5d_pct']:>+5.1f}% 市值{s['total_mv_yi']:>5.0f}亿 "
                  f"MA10+{s['ma10_slope_pct']:>+5.1f}% 链距={s['chain_distance']} [{s['trigger']}]")

    print(f"\n[Path 4] 最后一公里补涨爆发点 (Top{len(output['final_burst_points'])}):")
    for i, s in enumerate(output["final_burst_points"]):
        premium_icon = {"high": "★★★", "medium": "★★", "low": "★"}.get(
            s["expected_next_day_return_bias"], "★")
        print(f"   {i+1:>2}. {premium_icon} {s['stock']:<25s} "
              f"score={s['lagging_path_score']:>5.1f} conf={s['confidence']:.2f} [{s['expected_next_day_return_bias']}]")
        print(f"       {s['reason']}")

    nt = output["next_wave_prediction"]
    print(f"\n[Next Wave] 下一阶段主题扩散预测:")
    print(f"   → {nt['sub_theme']}  [{nt['direction']}]  confidence={nt['confidence']:.2f}")
    print(f"   {nt['reason']}")

    print(f"\n{'='*90}")


def main():
    json_path = find_latest_constituents()
    print(f"[Data] 读取: {json_path}")

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    themes = data["themes"]
    trade_date = data.get("trade_date", "unknown")
    print(f"[Data] {len(themes)} 个二级主题 / trade_date={trade_date}")

    output = build_output(themes, trade_date)

    out_path = os.path.join(CACHE_DIR, f"theme_lagging_path_{trade_date}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"✅ 输出: {out_path}")

    print_human_summary(output)


if __name__ == "__main__":
    main()
