#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Lagging Trade Selector V3.6-Trade
==================================
极简交易决策引擎：每天只输出 Top 1-3 可交易标的

TradeScore =
  0.40 × 资金确认强度（首板/放量/换手）
+ 0.30 × 产业链末端位置
+ 0.20 × 涨幅滞后但未启动
+ 0.10 × 市值弹性

三层收敛：
  Step 1: 路径收敛 - 只保留主升二级主题扩散链末端
  Step 2: 资金收敛 - 只保留已有明显资金介入
  Step 3: 交易收敛 - 只保留明天有交易价值

V3.6-Trade 升级：
  最强补涨方向二次拆解：
    - 核心工艺节点（技术）
    - 产业承接节点（公司）
    - 设备/材料节点（滞后补涨）
"""
import json
import os
import glob
from datetime import datetime
from collections import defaultdict

import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")


def safe_avg(values, default=0.0):
    vs = [v for v in values if v is not None]
    return float(np.mean(vs)) if vs else default


def compute_trade_score(stock, sub_theme, macro_stage):
    """
    TradeScore = 0.40*资金确认 + 0.30*末端位置 + 0.20*滞后未启动 + 0.10*市值弹性
    """
    limit_up_days = stock.get("limit_up_days", 0) or 0
    recent_up_days = stock.get("recent_up_days", 0) or 0
    avg_amount_5d = stock.get("avg_amount_5d", 0) or 0
    ma10_slope = stock.get("ma10_slope_pct", 0) or 0
    close_above_ma5 = stock.get("close_above_ma5", False)
    change_5d = stock.get("change_5d_pct", 0) or 0

    capital_score = 0
    if limit_up_days >= 1:
        capital_score += 50
    if recent_up_days >= 1 and limit_up_days == 0:
        capital_score += 30
    if close_above_ma5 and ma10_slope > 0:
        capital_score += 20
    if avg_amount_5d > 3e8:
        capital_score += 10
    capital_score = min(100, capital_score)

    position_weight = {
        "core_core": 20, "core": 40, "mid_expansion": 70, "edge": 100
    }.get(sub_theme.get("diffusion_position", "core"), 50)
    position_score = position_weight

    sub_avg_5d = sub_theme.get("avg_change_5d_pct", 0) or 0
    lag_gap = max(0, sub_avg_5d - change_5d)
    not_started = limit_up_days < 2 and change_5d < 15
    lag_score = min(100, lag_gap * 10) if not_started else 0
    if limit_up_days == 1 and change_5d < 12:
        lag_score = max(lag_score, 70)

    total_mv_wan = stock.get("total_mv_wan", 0) or 0
    if total_mv_wan <= 0:
        size_score = 50
    else:
        total_mv_yi = total_mv_wan / 10000
        if 20 <= total_mv_yi <= 100:
            size_score = 100
        elif 100 < total_mv_yi <= 300:
            size_score = 70
        elif total_mv_yi < 20:
            size_score = 60
        else:
            size_score = max(0, 50 - (total_mv_yi - 300) / 100)

    trade_score = round(
        0.40 * capital_score
        + 0.30 * position_score
        + 0.20 * lag_score
        + 0.10 * size_score
        , 2
    )

    if limit_up_days >= 1:
        expected = "加速" if limit_up_days >= 2 else "首板"
    elif capital_score >= 60 and close_above_ma5:
        expected = "首板"
    elif capital_score >= 40:
        expected = "异动"
    else:
        expected = "潜伏"

    confidence = min(0.95, trade_score / 100)

    return trade_score, expected, confidence, {
        "capital_score": capital_score,
        "position_score": position_score,
        "lag_score": lag_score,
        "size_score": size_score,
    }


def has_capital_signal(stock):
    """判断股票是否有资金异动信号"""
    limit_up_days = stock.get("limit_up_days", 0) or 0
    recent_up = stock.get("recent_up_days", 0) or 0
    close_above_ma5 = stock.get("close_above_ma5", False)
    ma10_slope = stock.get("ma10_slope_pct", 0) or 0
    change_5d = stock.get("change_5d_pct", 0) or 0
    avg_amount_5d = stock.get("avg_amount_5d", 0) or 0

    return (
        limit_up_days >= 1
        or recent_up >= 1
        or (close_above_ma5 and ma10_slope > 0 and change_5d > 0)
        or (avg_amount_5d > 5e8 and change_5d > 0)
    )


def classify_chain_node(stock, theme_cfg):
    """
    根据股票属性和主题配置，判断属于哪类产业链节点：
    - core_process: 核心工艺节点（技术）
    - industry_bearer: 产业承接节点（公司）
    - equipment_material: 设备/材料节点（滞后补涨）
    """
    # 从主题配置中获取 industry_roles
    industry_roles = theme_cfg.get("industry_roles", {}) if theme_cfg else {}
    business_dna = theme_cfg.get("business_dna_tags", []) if theme_cfg else []

    # 股票属性
    name = stock.get("name", "")
    total_mv_wan = stock.get("total_mv_wan", 0) or 0
    total_mv_yi = total_mv_wan / 10000
    avg_amount_5d = stock.get("avg_amount_5d", 0) or 0
    change_5d = stock.get("change_5d_pct", 0) or 0
    limit_up_days = stock.get("limit_up_days", 0) or 0

    # 关键词判断
    tech_keywords = ["科技", "技术", "电子", "芯片", "半导体", "微电", "光电", "软件", "信息", "智能"]
    material_keywords = ["材料", "化工", "金属", "铜", "铝", "钢", "膜", "粉", "晶", "硅"]
    equipment_keywords = ["设备", "装备", "机械", "仪器", "机器", "工", "制造"]

    # 核心工艺节点：技术含量高、市值中等、有涨停
    is_tech = any(kw in name for kw in tech_keywords)
    is_material = any(kw in name for kw in material_keywords)
    is_equipment = any(kw in name for kw in equipment_keywords)

    # 优先级判断
    if is_tech and limit_up_days >= 1:
        return "core_process"
    elif is_equipment and has_capital_signal(stock):
        return "equipment_material"
    elif is_material:
        return "equipment_material"
    elif is_tech:
        return "core_process"
    elif avg_amount_5d > 5e8 and total_mv_yi > 50:
        # 大成交额、中等市值以上 → 产业承接节点
        return "industry_bearer"
    elif total_mv_yi > 100:
        return "industry_bearer"
    else:
        # 默认根据涨幅和成交判断
        if change_5d > 5 or limit_up_days >= 1:
            return "core_process"
        else:
            return "equipment_material"


def build_chain_nodes(sub_theme, theme_cfg):
    """
    拆解子主题为3类产业链节点，并映射有资金异动的股票

    Returns:
        dict: {
            "core_process": {"desc": "", "stocks": [...]},
            "industry_bearer": {"desc": "", "stocks": [...]},
            "equipment_material": {"desc": "", "stocks": [...]}
        }
    """
    all_stocks = sub_theme.get("all_stocks", [])

    # 分类股票到各节点
    nodes = {
        "core_process": {"desc": "核心工艺节点（技术）", "stocks": []},
        "industry_bearer": {"desc": "产业承接节点（公司）", "stocks": []},
        "equipment_material": {"desc": "设备/材料节点（滞后补涨）", "stocks": []},
    }

    for stock in all_stocks:
        if not has_capital_signal(stock):
            continue

        node_type = classify_chain_node(stock, theme_cfg)
        nodes[node_type]["stocks"].append(stock)

    # 每个节点只保留Top 3（按资金强度排序）
    for node_type in nodes:
        stocks = nodes[node_type]["stocks"]
        # 按涨停数、涨幅、成交额排序
        stocks.sort(key=lambda s: (
            -(s.get("limit_up_days") or 0),
            -(s.get("change_5d_pct") or 0),
            -(s.get("avg_amount_5d") or 0)
        ))
        nodes[node_type]["stocks"] = stocks[:3]

    return nodes


def format_stock_info(stock):
    """格式化股票信息"""
    name = stock.get("name", "")
    code = stock.get("ts_code", "")
    change_5d = stock.get("change_5d_pct", 0) or 0
    limit_up = stock.get("limit_up_days", 0) or 0
    avg_amount = stock.get("avg_amount_5d", 0) or 0

    return {
        "name": name,
        "code": code,
        "change_5d": round(change_5d, 1),
        "limit_up_days": limit_up,
        "avg_amount_yi": round(avg_amount / 1e8, 1),
        "signal": "首板" if limit_up >= 1 else ("放量" if avg_amount > 3e8 else "异动")
    }


def run_trade_selector():
    # 读取成分股数据
    pattern = os.path.join(CACHE_DIR, "theme3_constituents_*.json")
    files = sorted(glob.glob(pattern))
    if not files:
        print("❌ 未找到 theme3_constituents_*.json")
        return None
    json_path = files[-1]
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    themes = data["themes"]
    trade_date = data.get("trade_date", "unknown")

    # 读取主题配置（用于获取 industry_roles）
    theme_cfg_path = os.path.join(BASE_DIR, "theme3.json")
    theme_cfg_map = {}
    if os.path.exists(theme_cfg_path):
        with open(theme_cfg_path, "r", encoding="utf-8") as f:
            theme_cfg_data = json.load(f)
        flat_map = theme_cfg_data.get("THEME_FLAT_MAP", {})
        for name, cfg in flat_map.items():
            theme_cfg_map[name] = cfg

    # Step 1: 识别主升一级主题
    macro_map = defaultdict(list)
    for sub in themes:
        macro_map[sub["top_category"]].append(sub)

    macro_stats = []
    for cat, subs in macro_map.items():
        all_stocks = []
        for s in subs:
            all_stocks.extend(s["stocks"])
        if not all_stocks:
            continue

        n_limit_up = sum(1 for s in all_stocks if (s.get("limit_up_days") or 0) >= 1)
        avg_chg5 = safe_avg([s.get("change_5d_pct", 0) or 0 for s in all_stocks])
        total_amount = sum(s.get("avg_amount_5d", 0) or 0 for s in all_stocks)

        strength = n_limit_up * 10 + max(0, avg_chg5) * 5 + total_amount / 1e8 * 0.2

        if avg_chg5 > 3 and n_limit_up >= 3:
            stage = "主升"
        elif avg_chg5 > 0 and n_limit_up >= 1:
            stage = "启动"
        elif avg_chg5 > -2:
            stage = "分歧"
        else:
            stage = "退潮"

        sub_list = []
        for sub in subs:
            sub_stocks = sub["stocks"]
            if len(sub_stocks) < 5:
                continue
            sub_n_limit = sum(1 for s in sub_stocks if (s.get("limit_up_days") or 0) >= 1)
            sub_chg5 = safe_avg([s.get("change_5d_pct", 0) or 0 for s in sub_stocks])
            sub_amount = sum(s.get("avg_amount_5d", 0) or 0 for s in sub_stocks)
            sub_strength = sub_n_limit * 10 + max(0, sub_chg5) * 4 + sub_amount / 1e8 * 0.2
            sub_list.append({
                "theme_name": sub["theme_name"],
                "n_stocks": len(sub_stocks),
                "avg_change_5d_pct": sub_chg5,
                "n_limit_up": sub_n_limit,
                "strength": sub_strength,
                "all_stocks": sub_stocks,
            })

        sub_list.sort(key=lambda x: -x["strength"])
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

        macro_stats.append({
            "macro_theme": cat,
            "strength": strength,
            "stage": stage,
            "n_limit_up": n_limit_up,
            "sub_themes": sub_list,
        })

    macro_stats.sort(key=lambda x: -x["strength"])

    # Step 2: 三层收敛 - 筛选可交易标的
    all_candidates = []
    for macro in macro_stats[:3]:
        if macro["stage"] in ("退潮",):
            continue

        edge_subs = [s for s in macro["sub_themes"]
                     if s["diffusion_position"] in ("mid_expansion", "edge")]

        for sub in edge_subs:
            for stock in sub["all_stocks"]:
                limit_up_days = stock.get("limit_up_days", 0) or 0
                recent_up = stock.get("recent_up_days", 0) or 0
                close_above_ma5 = stock.get("close_above_ma5", False)
                ma10_slope = stock.get("ma10_slope_pct", 0) or 0
                change_5d = stock.get("change_5d_pct", 0) or 0

                has_signal = (
                    limit_up_days >= 1
                    or recent_up >= 1
                    or (close_above_ma5 and ma10_slope > 0 and change_5d > 0)
                )
                if not has_signal:
                    continue

                if change_5d > 20 and limit_up_days >= 2:
                    continue

                trade_score, expected, confidence, sub_scores = compute_trade_score(
                    stock, sub, macro["stage"]
                )

                if trade_score < 50:
                    continue

                all_candidates.append({
                    "stock": f"{stock['name']}({stock['ts_code']})",
                    "stock_code": stock["ts_code"],
                    "stock_name": stock["name"],
                    "sub_theme": sub["theme_name"],
                    "macro_theme": macro["macro_theme"],
                    "diffusion_position": sub["diffusion_position"],
                    "trade_score": trade_score,
                    "expected_move": expected,
                    "confidence": confidence,
                    "reason": (
                        f"{sub['theme_name']}({sub['diffusion_position']}) "
                        f"5日+{change_5d:.1f}% 连板{limit_up_days} "
                        f"MA10+{ma10_slope:.1f}% "
                        f"资金分{sub_scores['capital_score']}"
                    ),
                    "_stock": stock,
                })

    all_candidates.sort(key=lambda x: -x["trade_score"])
    top3 = all_candidates[:3]

    # Step 4: 最强补涨方向 + 二次拆解
    best_sub = None
    best_sub_info = {"name": "", "direction": "", "why": "", "chain_nodes": {}}

    if macro_stats:
        top_macro = macro_stats[0]
        edge_subs = [s for s in top_macro["sub_themes"]
                     if s["diffusion_position"] in ("mid_expansion", "edge") and s["n_limit_up"] >= 1]
        if edge_subs:
            best_sub = max(edge_subs, key=lambda x: x["strength"])

            # 获取主题配置
            theme_cfg = theme_cfg_map.get(best_sub["theme_name"], {})

            # 二次拆解：产业链节点
            chain_nodes = build_chain_nodes(best_sub, theme_cfg)

            best_sub_info = {
                "name": best_sub["theme_name"],
                "direction": "inflow",
                "why": f"主升'{top_macro['macro_theme']}'扩散末端，涨停{best_sub['n_limit_up']}只，5日+{best_sub['avg_change_5d_pct']:.1f}%",
                "chain_nodes": {
                    node_type: {
                        "desc": node_data["desc"],
                        "stocks": [format_stock_info(s) for s in node_data["stocks"]]
                    }
                    for node_type, node_data in chain_nodes.items()
                    if node_data["stocks"]  # 只保留有股票的节点
                }
            }
        else:
            best_sub = top_macro["sub_themes"][0] if top_macro["sub_themes"] else None
            best_sub_info = {
                "name": best_sub["theme_name"] if best_sub else "",
                "direction": "hold",
                "why": f"核心主题'{top_macro['macro_theme']}'，无明确扩散末端",
                "chain_nodes": {}
            }

    # Step 5: 风险切换信号
    risk_signal = None
    if len(macro_stats) >= 2:
        top1, top2 = macro_stats[0], macro_stats[1]
        if top1["stage"] in ("分歧", "退潮") and top2["stage"] in ("启动", "主升"):
            risk_signal = {
                "type": "切换",
                "from_theme": top1["macro_theme"],
                "to_theme": top2["macro_theme"],
                "confidence": 0.7,
                "reason": f"'{top1['macro_theme']}'{top1['stage']}，资金可能切换至'{top2['macro_theme']}'"
            }
        elif top1["stage"] == "分歧":
            risk_signal = {
                "type": "分歧",
                "from_theme": top1["macro_theme"],
                "to_theme": "",
                "confidence": 0.5,
                "reason": f"'{top1['macro_theme']}'进入分歧，注意龙头分歧风险"
            }

    if not risk_signal:
        risk_signal = {
            "type": "无",
            "from_theme": "",
            "to_theme": "",
            "confidence": 0.0,
            "reason": "当前无明确切换信号"
        }

    # 输出结果
    output = {
        "trade_date": trade_date,
        "engine": "Lagging Trade Selector V3.6-Trade",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "top_trade_candidates": [
            {
                "stock": c["stock"],
                "score": c["trade_score"],
                "expected_move": c["expected_move"],
                "reason": c["reason"],
                "confidence": c["confidence"],
            }
            for c in top3
        ],
        "best_sub_theme": best_sub_info,
        "risk_signal": risk_signal,
    }

    # 保存JSON
    out_json = os.path.join(CACHE_DIR, f"theme_trade_selector_{trade_date}.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # 打印极简输出
    print("\n" + "=" * 70)
    print(f"  Lagging Trade Selector V3.6-Trade  |  {trade_date}")
    print("=" * 70)

    print("\n【Top 3 可交易标的】")
    if top3:
        for i, c in enumerate(top3, 1):
            print(f"  {i}. {c['stock_name']:<10}({c['stock_code']}) "
                  f"Score={c['trade_score']:.1f} "
                  f"[{c['expected_move']}] conf={c['confidence']:.2f}")
            print(f"     {c['reason']}")
    else:
        print("  （无符合条件的可交易标的）")

    print(f"\n【最强补涨方向】")
    if best_sub_info.get("name"):
        print(f"  → {best_sub_info['name']} [{best_sub_info['direction']}]")
        print(f"     {best_sub_info['why']}")

        # 打印产业链节点拆解
        chain_nodes = best_sub_info.get("chain_nodes", {})
        if chain_nodes:
            print(f"\n  ┌─ 产业链节点拆解 ─────────────────────────────────")
            for node_type, node_data in chain_nodes.items():
                stocks = node_data.get("stocks", [])
                if stocks:
                    node_name = {
                        "core_process": "核心工艺",
                        "industry_bearer": "产业承接",
                        "equipment_material": "设备/材料"
                    }.get(node_type, node_type)
                    print(f"  │ [{node_name}]")
                    for s in stocks:
                        signal = s.get("signal", "")
                        print(f"  │   • {s['name']:<8} 5日+{s['change_5d']:+.1f}% "
                              f"连板{s['limit_up_days']} [{signal}]")
            print(f"  └──────────────────────────────────────────────────")

    print(f"\n【风险切换信号】")
    print(f"  类型: {risk_signal['type']}")
    if risk_signal['type'] != "无":
        print(f"  {risk_signal['from_theme']} → {risk_signal['to_theme']}")
        print(f"  {risk_signal['reason']}")

    print("\n" + "=" * 70)
    print(f"✅ JSON已保存: {out_json}")

    return output


if __name__ == "__main__":
    run_trade_selector()
