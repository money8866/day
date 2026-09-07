#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A股 Sector Structure + T60/T120 中长线布局分析器 V2.0（脚本版）

方法论: 三维强度 SHORT(T+1~5) / MID(T+20) / LONG(T+60~120)；
四类型 A共振 / B短弱长强 / C短强长弱 / D全弱；
LONG_SCORE 八因子加权(盈利趋势25% + 行业景气20% + 60/120日趋势20% +
相对强度10% + 机构资金10% + 竞争格局5% + 估值5% + 产业长期逻辑5%)；
DATA_INSUFFICIENT 诚实原则；T120 分级 CORE/ACCUMULATION/WATCH/AVOID；
严格十节 Markdown 输出。短线引擎(trend/sentiment/composite/trade_action)只读不改。

用法:
    python -X utf8 sector_intel_v2.py [YYYYMMDD]

输入:
    report_daily/theme_scores_v2_YYYYMMDD.csv    主题日线引擎输出
    report_daily/theme_scores_v2_*.csv           历史序列(持续性因子)
    report_daily/theme_stock_map_latest_v2.json  主题成分映射
    sector_intel_v2_fact.json                    基本面事实表(报告日快照)

输出:
    report_daily/sector_intel_v2_T120_YYYYMMDD.md
"""

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(BASE, "report_daily")
FACT_PATH = os.path.join(BASE, "sector_intel_v2_fact.json")
MAP_PATH = os.path.join(REPORT_DIR, "theme_stock_map_latest_v2.json")
CSV_GLOB = os.path.join(REPORT_DIR, "theme_scores_v2_*.csv")
OUT_PATH = os.path.join(REPORT_DIR, "sector_intel_v2_T120_{date}.md")

HIST_COLS = ["theme", "composite_score", "t_pct_above_ma20", "t_pct_above_ma60",
             "t_avg_ret_20", "t_rel_ret_10", "fund_net_ratio", "rank"]

W_FACTORS = [
    ("盈利趋势", 0.25), ("行业景气", 0.20), ("60/120日趋势", 0.20), ("相对强度", 0.10),
    ("机构资金", 0.10), ("竞争格局", 0.05), ("估值", 0.05), ("产业长期逻辑", 0.05),
]
FACT_FACTOR_NAMES = ["盈利趋势", "行业景气", "竞争格局", "估值", "产业长期逻辑"]

LONG_STRONG = 70
LONG_WEAK = 60
SHORT_OK = 60
COMPOSITE_OK = 65
MID_OK = 60
TYPE_A_LONG = 65
MIN_FACT_FACTORS = 3

FACT_ALIASES = {
    "黄金": ["贵金属"],
    "工业金属": ["有色金属", "基本金属", "铜", "铝"],
    "银行": [],
    "煤炭": [],
    "证券": ["券商", "非银"],
    "电力": ["公用事业", "电网"],
    "传媒": ["媒体"],
    "游戏": [],
    "钢铁": ["特钢", "普钢"],
    "地产链": ["房地产", "地产", "建材", "房地产链"],
}

TYPE_LABEL = {"A": "A·共振", "B": "B·短弱长强", "C": "C·短强长弱", "D": "D·全弱"}
TYPE_ACTION = {
    "A": "趋势共振：持有为主、回调加仓；留意高潮预警(climax_warning)与拥挤度，破位或景气证伪再减。",
    "B": "利用短线退潮分批低吸，T60/T120 周期持有；不追短线反弹，跌破 MA60 或基本面证伪离场。",
    "C": "仅短线情绪参与、快进快出；严禁纳入 T120 中长线组合。",
    "D": "回避；等待 LONG_SCORE 或 SHORT 任一修复后再评估。",
}
GRADE_LABEL = {
    "CORE": "CORE·核心仓(≥80)",
    "ACCUMULATION": "ACCUMULATION·积累仓(70-79)",
    "WATCH": "WATCH·观察(60-69)",
    "AVOID": "AVOID·回避(<60)",
    "DATA_INSUFFICIENT": "DATA_INSUFFICIENT·数据不足",
}
GRADE_ACTION = {
    "CORE": "底仓品种：回调分批建仓，持有周期 T60-T120；跟踪盈利上修与资金持续性。",
    "ACCUMULATION": "逢低分批积累：不追高，等待短线情绪出清后的低吸区；验证因子拐点后可升级。",
    "WATCH": "观察名单：等待 LONG_SCORE 提升催化(盈利上修/机构回流/景气确认)。",
    "AVOID": "不建立中长线仓位；若为 C 型可只做短线。",
    "DATA_INSUFFICIENT": "暂不评级：基本面事实缺失，仅提供量化侧预览。",
}


def clamp(x):
    return max(0.0, min(100.0, float(x)))


def num(row, key):
    try:
        v = row[key]
    except (KeyError, IndexError):
        return None
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if np.isnan(f):
        return None
    return f


def map_earn(yoy):
    if yoy >= 80:
        return 92.0
    if yoy >= 50:
        return 85.0
    if yoy >= 30:
        return 75.0
    if yoy >= 10:
        return 65.0
    if yoy >= 0:
        return 58.0
    if yoy >= -15:
        return 40.0
    if yoy >= -30:
        return 25.0
    return 12.0


def map_slope(x):
    if x >= 0.3:
        return 92.0
    if x >= 0.2:
        return 85.0
    if x >= 0.1:
        return 72.0
    if x >= 0.03:
        return 62.0
    if x >= 0:
        return 52.0
    if x >= -0.03:
        return 38.0
    return 25.0


def map_rel(x):
    if x >= 8:
        return 90.0
    if x >= 5:
        return 80.0
    if x >= 2:
        return 70.0
    if x >= 0:
        return 60.0
    if x >= -2:
        return 48.0
    if x >= -5:
        return 35.0
    return 22.0


def map_ret3(x):
    if x >= 3:
        return 90.0
    if x >= 1:
        return 75.0
    if x >= 0:
        return 60.0
    if x >= -1:
        return 45.0
    if x >= -3:
        return 30.0
    return 15.0


def map_ret20(x):
    if x >= 8:
        return 90.0
    if x >= 5:
        return 80.0
    if x >= 2:
        return 70.0
    if x >= 0:
        return 58.0
    if x >= -3:
        return 42.0
    return 25.0


def score_fund(ratio, growth):
    if ratio >= 0.05:
        s = 85.0
    elif ratio >= 0.03:
        s = 75.0
    elif ratio >= 0.01:
        s = 65.0
    elif ratio >= 0:
        s = 55.0
    elif ratio >= -0.01:
        s = 45.0
    elif ratio >= -0.03:
        s = 35.0
    else:
        s = 20.0
    if growth is not None:
        if growth > 0.05:
            s = min(100.0, s + 8.0)
        elif growth > 0:
            s = min(100.0, s + 4.0)
        elif growth < -0.3:
            s = max(0.0, s - 10.0)
        elif growth < -0.1:
            s = max(0.0, s - 6.0)
    return s


def load_fact():
    try:
        with open(FACT_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[warn] 事实表读取失败({e})，所有主题将标 DATA_INSUFFICIENT", file=sys.stderr)
        return {}


def load_map():
    try:
        with open(MAP_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[warn] 成分映射读取失败({e})", file=sys.stderr)
        return {}


def match_fact(theme, fact_themes):
    if theme in fact_themes:
        return theme
    for f in fact_themes:
        if f and (f in theme or theme in f):
            return f
    for f, aliases in FACT_ALIASES.items():
        if f not in fact_themes:
            continue
        for a in aliases:
            if a == theme or a in theme or theme in a:
                return f
    return None


def load_history():
    frames = []
    for fp in sorted(glob.glob(CSV_GLOB)):
        m = re.search(r"(\d{8})\.csv$", fp)
        if not m:
            continue
        try:
            df = pd.read_csv(fp, encoding="utf-8-sig")
        except Exception:
            continue
        cols = [c for c in HIST_COLS if c in df.columns]
        if "theme" not in cols:
            continue
        d = df[cols].copy()
        d["src_date"] = m.group(1)
        frames.append(d)
    if not frames:
        return pd.DataFrame(columns=HIST_COLS + ["src_date"])
    return pd.concat(frames, ignore_index=True)


def persistence(hist, theme, col, thresh=50.0):
    if hist.empty:
        return None
    s = pd.to_numeric(hist.loc[hist["theme"] == theme, col], errors="coerce").dropna()
    if s.empty:
        return None
    return float((s >= thresh).mean() * 100.0)


def compute_short(row):
    r3 = num(row, "t_avg_ret_3")
    sent = num(row, "sentiment_score")
    if r3 is None and sent is None:
        return None
    parts = []
    if r3 is not None:
        parts.append((map_ret3(r3), 0.20))
    if sent is not None:
        parts.append((clamp(sent), 0.25))
    res = num(row, "s_resonance")
    if res is not None:
        parts.append((clamp(res * 100.0), 0.15))
    for col, w in [("s_focus_score", 0.10), ("s_money_resonance", 0.05),
                   ("t_pct_above_ma5", 0.05)]:
        v = num(row, col)
        if v is not None:
            parts.append((clamp(v), w))
    zt = num(row, "s_zt_count") or 0.0
    mlb = num(row, "s_multi_lb_count") or 0.0
    parts.append((min(100.0, zt * 18.0 + mlb * 12.0), 0.10))
    ech = num(row, "s_echelon_levels")
    if ech is not None:
        parts.append((min(100.0, ech * 25.0), 0.10))
    tw = sum(w for _, w in parts)
    return sum(s * w for s, w in parts) / tw


def compute_mid(row, hist, theme):
    parts = []
    r20 = num(row, "t_avg_ret_20")
    if r20 is not None:
        parts.append((map_ret20(r20), 0.30))
    p20 = num(row, "t_pct_above_ma20")
    if p20 is not None:
        parts.append((clamp(p20), 0.25))
    p60 = num(row, "t_pct_above_ma60")
    if p60 is not None:
        parts.append((clamp(p60), 0.20))
    sl = num(row, "t_avg_slope_10")
    if sl is not None:
        parts.append((map_slope(sl), 0.15))
    pers = persistence(hist, theme, "t_pct_above_ma20")
    if pers is not None:
        parts.append((pers, 0.10))
    if not parts:
        return None
    tw = sum(w for _, w in parts)
    return sum(s * w for s, w in parts) / tw


def compute_trend_factor(row, hist, theme):
    p60 = num(row, "t_pct_above_ma60")
    if p60 is None:
        return None
    mid_ok = num(row, "t_mid_trend_ok")
    mid_ok_s = 100.0 if (mid_ok is not None and mid_ok >= 0.5) else 40.0
    sl = num(row, "t_avg_slope_60")
    slope_s = map_slope(sl) if sl is not None else 52.0
    pers = persistence(hist, theme, "t_pct_above_ma60")
    if pers is None:
        pers = p60
    return 0.55 * p60 + 0.20 * mid_ok_s + 0.15 * slope_s + 0.10 * pers


def compute_long(row, fact, hist, theme):
    gaps = []
    vals = {k: None for k, _ in W_FACTORS}
    if fact:
        yoy = fact.get("h1_yoy_median")
        if yoy is not None:
            try:
                vals["盈利趋势"] = map_earn(float(yoy))
            except (TypeError, ValueError):
                pass
        else:
            gaps.append("盈利趋势(h1_yoy 缺)")
        for fname, fkey in [("行业景气", "climate_score"), ("竞争格局", "comp_score"),
                            ("估值", "val_score"), ("产业长期逻辑", "long_logic_score")]:
            v = fact.get(fkey)
            if v is not None:
                vals[fname] = clamp(v)
            else:
                gaps.append(fname)
    vals["60/120日趋势"] = compute_trend_factor(row, hist, theme)
    rel_v = num(row, "t_rel_ret_10")
    if rel_v is not None:
        vals["相对强度"] = map_rel(rel_v)
    fr = num(row, "fund_net_ratio")
    if fr is not None:
        vals["机构资金"] = score_fund(fr, num(row, "fund_growth"))
    fact_avail = sum(1 for k in FACT_FACTOR_NAMES if vals[k] is not None)
    if fact_avail < MIN_FACT_FACTORS:
        return None, vals, ["基本面事实缺失 → DATA_INSUFFICIENT(可评分因子不足)"]
    tw = sum(w for k, w in W_FACTORS if vals[k] is not None)
    lng = sum(vals[k] * w for k, w in W_FACTORS if vals[k] is not None) / tw
    return lng, vals, gaps


def classify(short, mid, lng, composite):
    if lng is None:
        return "D"
    s_ok = ((short is not None and short >= SHORT_OK)
            or (composite is not None and composite >= COMPOSITE_OK))
    if lng >= LONG_STRONG and not s_ok:
        return "B"
    if s_ok and mid is not None and mid >= MID_OK and lng >= TYPE_A_LONG:
        return "A"
    if s_ok and lng < LONG_WEAK:
        return "C"
    return "D"


def grade(lng):
    if lng is None:
        return "DATA_INSUFFICIENT"
    if lng >= 80:
        return "CORE"
    if lng >= 70:
        return "ACCUMULATION"
    if lng >= 60:
        return "WATCH"
    return "AVOID"


def f1(x):
    return "—" if x is None else f"{x:.1f}"


def md_table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(lines)


def top_members(mapj, theme, k=8):
    themes = mapj.get("themes", {}) if isinstance(mapj, dict) else {}
    items = themes.get(theme, [])
    out = []
    for it in sorted(items, key=lambda x: x.get("score", 0) or 0, reverse=True)[:k]:
        out.append(f"{it.get('name','?')}({it.get('code','?')}, {it.get('score', 0)})")
    return "、".join(out) if out else "（map 无该主题成分）"


def build_records(df, hist, factj):
    fact_themes = list((factj.get("themes") or {}).keys())
    records = []
    for _, row in df.iterrows():
        theme = str(row.get("theme", "")).strip()
        fname = match_fact(theme, fact_themes)
        fact = (factj.get("themes") or {}).get(fname) if fname else None
        short = compute_short(row)
        mid = compute_mid(row, hist, theme)
        lng, parts, gaps = compute_long(row, fact, hist, theme)
        g = grade(lng)
        records.append({
            "rank": num(row, "rank"),
            "theme": theme,
            "n_stocks": num(row, "n_stocks"),
            "composite": num(row, "composite_score"),
            "lifecycle": str(row.get("lifecycle", "—")),
            "trade_action": str(row.get("trade_action", "—")),
            "short": short,
            "mid": mid,
            "long": lng,
            "type": classify(short, mid, lng, num(row, "composite_score")),
            "grade": g,
            "parts": parts,
            "gaps": gaps,
            "fact_name": fname,
            "fact": fact,
            "mainline_type": str(row.get("mainline_type", "—")),
            "mainline_quality": num(row, "mainline_quality"),
            "mainline_quality_label": str(row.get("mainline_quality_label", "—")),
            "mti_level": str(row.get("mti_level", "—")),
            "migration_direction": str(row.get("migration_direction", "—")),
            "migration_score": num(row, "migration_score"),
            "gate_tier": str(row.get("gate_tier", "—")),
            "days_strong": num(row, "days_strong"),
        })
    records.sort(key=lambda r: (r["rank"] if r["rank"] is not None else 999))
    return records


def render_report(date, records, factj, mapj, hist_days):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    L = []
    L.append(f"# A股 Sector Structure + T60/T120 中长线布局分析器 V2.0（脚本版）")
    L.append("")
    L.append(f"- 报告日：**{date}**　生成时间：{now}")
    L.append(f"- 维度定义：SHORT=T+1~5（情绪/资金）、MID=T+20（波段趋势）、LONG=T+60~120（投资质量）")
    L.append("- 规则：短线引擎输出(composite/lifecycle/trade_action)只读不改；"
             "LONG_SCORE 八因子加权=盈利趋势25%+行业景气20%+60/120日趋势20%+相对强度10%"
             "+机构资金10%+竞争格局5%+估值5%+产业长期逻辑5%；缺因子按剩余权重归一并在第十节披露。")
    L.append("")

    n = len(records)
    type_counts = {}
    for r in records:
        type_counts[r["type"]] = type_counts.get(r["type"], 0) + 1
    fact_asof = str(factj.get("asof", "—"))
    asof_warn = "" if fact_asof == date else f"⚠ 事实表 asof={fact_asof} ≠ 报告日 {date}"

    L.append("## 一、数据源与可用性声明")
    L.append("")
    L.append(f"- 主题日线：`report_daily/theme_scores_v2_{date}.csv`（{n} 个主题，短中长量化字段齐全）")
    L.append(f"- 历史序列：`theme_scores_v2_*.csv` 共 {hist_days} 个交易日（用于持续性因子；"
             f"60/120 日完整主题序列不可得，60/120 日趋势以成分股 MA60 占比 t_pct_above_ma60 与 t_mid_trend_ok 为准）")
    L.append(f"- 基本面事实表：`sector_intel_v2_fact.json`（asof={fact_asof}）{asof_warn}")
    L.append("- 成分映射：`theme_stock_map_latest_v2.json`")
    L.append("- 原则：**DATA_INSUFFICIENT**——基本面可评分因子 <3/5 的主题不出 T120 评级，只在第十节给量化侧预览；"
             "任何缺项不估算、不伪造。")
    L.append("")

    L.append("## 二、大盘环境与总仓位")
    L.append("")
    market = factj.get("market") or {}
    if market:
        L.append(f"- 市场状态：**{market.get('state','—')}**　风险：{market.get('risk','—')}　"
                 f"目标总仓位：**{market.get('target_position_pct','—')}%**")
        L.append(f"- 说明：{market.get('note','')}")
        rows = [[m.get("name"), f1(m.get("ret_q3")), f1(m.get("ret_2026H1"))] for m in market.get("index", [])]
        L.append("")
        L.append(md_table(["指数", "2026Q3 涨跌(%)", "2026H1 涨跌(%)"], rows))
        L.append("")
        L.append("> Q3 普跌 + H1 高涨幅 → 高位结构性退潮环境，总仓位受 target_position_pct 约束，"
                 "中长线布局以低吸积累为主、不追高。")
    else:
        L.append("- **DATA_INSUFFICIENT**：事实表无 market 节，总仓位与大盘判断缺失，需先刷新事实表。")
    L.append("")

    L.append("## 三、板块结构总览（全景表）")
    L.append("")
    rows = []
    for r in records:
        rows.append([
            f1(r["rank"]), r["theme"], f1(r["n_stocks"]), f1(r["composite"]), r["lifecycle"],
            f1(r["short"]), f1(r["mid"]), f1(r["long"]),
            TYPE_LABEL[r["type"]], GRADE_LABEL[r["grade"]].split("·")[0], r["trade_action"],
        ])
    L.append(md_table(["rank", "主题", "成分数", "综合分(短线)", "生命周期",
                       "SHORT", "MID", "LONG", "类型", "T120", "短线引擎动作"], rows))
    L.append("")

    L.append("## 四、四类型判定（A共振 / B短弱长强 / C短强长弱 / D全弱）")
    L.append("")
    L.append(f"判据：短线热 = SHORT≥{SHORT_OK} 或 短线综合≥{COMPOSITE_OK}；MID_OK={MID_OK}；"
             f"LONG 强=≥{LONG_STRONG}、弱=<{LONG_WEAK}；A 另需 LONG≥{TYPE_A_LONG}。")
    L.append("")
    for t in ["A", "B", "C", "D"]:
        members = [r for r in records if r["type"] == t]
        names = "、".join(f"{r['theme']}({f1(r['long'])})" for r in members) if members else "（无）"
        L.append(f"- **{TYPE_LABEL[t]}**：{len(members)} 个 → {names}")
    L.append("")
    L.append(f"- **B 型（本版重点）操作口径**：{TYPE_ACTION['B']}")
    L.append("")

    L.append("## 五、双轨解构：SHORT_STRENGTH vs INVESTMENT_QUALITY")
    L.append("")
    L.append("核心矛盾处理：短线综合分与中长线投资质量分离评估，避免“高分清仓/低分踏空”误判。")
    L.append("")
    conflicted = [r for r in records
                  if r["type"] in ("B", "C") or
                  (r["short"] is not None and r["long"] is not None and abs(r["short"] - r["long"]) >= 25)]
    if conflicted:
        rows = []
        for r in conflicted:
            if r["type"] == "B":
                judge = "短线冷却/中长线优 → 分批低吸窗口"
            elif r["type"] == "C":
                judge = "短线热/中长线弱 → 只做短线，不入 T120"
            else:
                judge = "双轨背离 → 事件驱动，按类型处置"
            rows.append([r["theme"], f1(r["composite"]), f1(r["short"]), r["trade_action"],
                         f1(r["long"]), TYPE_LABEL[r["type"]], judge])
        L.append(md_table(["主题", "短线综合", "SHORT", "短线引擎动作", "LONG(投资质量)", "类型", "处置"], rows))
    else:
        L.append("- 本期无显著双轨背离主题。")
    L.append("")

    L.append("## 六、LONG_SCORE 八因子明细")
    L.append("")
    scored = [r for r in records if r["long"] is not None]
    if scored:
        rows = []
        for r in scored:
            p = r["parts"]
            rows.append([
                r["theme"], f1(p["盈利趋势"]), f1(p["行业景气"]), f1(p["60/120日趋势"]),
                f1(p["相对强度"]), f1(p["机构资金"]), f1(p["竞争格局"]), f1(p["估值"]),
                f1(p["产业长期逻辑"]), f1(r["long"]), GRADE_LABEL[r["grade"]].split("·")[0],
                "；".join(r["gaps"]) if r["gaps"] else "—",
            ])
        L.append(md_table(["主题", "盈利25%", "景气20%", "趋势20%", "相对10%", "机构10%",
                           "竞争5%", "估值5%", "逻辑5%", "LONG", "T120", "缺口"], rows))
    else:
        L.append("- **DATA_INSUFFICIENT**：无主题满足最低可评分因子要求。")
    L.append("")

    L.append("## 七、T120 分级与操作建议")
    L.append("")
    for g in ["CORE", "ACCUMULATION", "WATCH", "AVOID", "DATA_INSUFFICIENT"]:
        members = [r for r in records if r["grade"] == g]
        if not members:
            continue
        L.append(f"### {GRADE_LABEL[g]}（{len(members)} 个）")
        L.append("")
        rows = [[r["theme"], f1(r["long"]), TYPE_LABEL[r["type"]],
                 f1(r["short"]), f1(r["mid"])] for r in members]
        L.append(md_table(["主题", "LONG", "类型", "SHORT", "MID"], rows))
        L.append("")
        L.append(f"> {GRADE_ACTION[g]}")
        L.append("")
    L.append("")

    L.append("## 八、主线质量与轮动信号")
    L.append("")
    L.append("轮动判定：CONFIRMED=迁移方向 up 且迁移分≥10 且主线质量≥60；WATCH=迁移方向 up 但未达确认线。")
    L.append("")
    confirmed, watch = [], []
    for r in records:
        if r["migration_direction"] == "up":
            if (r["migration_score"] or 0) >= 10 and (r["mainline_quality"] or 0) >= 60:
                confirmed.append(r)
            else:
                watch.append(r)
    L.append(f"- **ROTATION CONFIRMED**：" + ("、".join(f"{r['theme']}({f1(r['migration_score'])})" for r in confirmed) if confirmed else "（无）"))
    L.append(f"- **ROTATION WATCH**：" + ("、".join(f"{r['theme']}({f1(r['migration_score'])})" for r in watch) if watch else "（无）"))
    L.append("")
    mig = [r for r in records if r["migration_direction"] not in ("—", "")]
    if mig:
        rows = [[r["theme"], r["migration_direction"], f1(r["migration_score"]), r["mainline_type"],
                 f1(r["mainline_quality"]), r["mti_level"], r["gate_tier"], f1(r["days_strong"])]
                for r in mig]
        L.append(md_table(["主题", "迁移方向", "迁移分", "主线类型", "主线质量", "MTI", "Gate", "连强天数"], rows))
    L.append("")

    L.append("## 九、重点主题深度解读")
    L.append("")
    deep = [r for r in records if r["fact"] and (r["grade"] in ("CORE", "ACCUMULATION", "WATCH") or r["type"] in ("A", "B"))]
    if not deep:
        L.append("- 本期无可深度解读主题（评级与事实覆盖均不足）。")
        L.append("")
    for r in deep:
        fact = r["fact"]
        vintage = str(fact.get("h1_vintage", "—"))
        stale = "⚠(口径未刷新,不确定性源)" if "未刷新" in vintage else ""
        L.append(f"#### {r['theme']}（{TYPE_LABEL[r['type']]} · {GRADE_LABEL[r['grade']]} · LONG {f1(r['long'])}）")
        L.append("")
        L.append(f"- 三维强度：SHORT {f1(r['short'])} / MID {f1(r['mid'])} / LONG {f1(r['long'])}；"
                 f"短线引擎：composite {f1(r['composite'])}，lifecycle {r['lifecycle']}，动作「{r['trade_action']}」")
        p = r["parts"]
        L.append(f"- 八因子：盈利 {f1(p['盈利趋势'])}｜景气 {f1(p['行业景气'])}｜趋势 {f1(p['60/120日趋势'])}"
                 f"｜相对 {f1(p['相对强度'])}｜机构 {f1(p['机构资金'])}｜竞争 {f1(p['竞争格局'])}"
                 f"｜估值 {f1(p['估值'])}｜逻辑 {f1(p['产业长期逻辑'])}")
        pe = fact.get("pe")
        pb = fact.get("pb")
        pe_s = "—" if pe is None else f"{pe}"
        pb_s = "—" if pb is None else f"{pb}"
        yoy = fact.get("h1_yoy_median")
        yoy_s = "—" if yoy is None else f"{yoy}"
        L.append(f"- 基本面（{vintage}{stale}）：{fact.get('sw_sector','—')} · PE {pe_s} / PB {pb_s}"
                 f" · 归母净利中位 {yoy_s}%")
        L.append(f"  - 景气：{fact.get('climate','—')}（{fact.get('climate_score','—')}）")
        L.append(f"  - 格局：{fact.get('competition','—')}（{fact.get('comp_score','—')}）")
        L.append(f"  - 估值：{fact.get('valuation','—')}（{fact.get('val_score','—')}）")
        L.append(f"  - 长逻辑：{fact.get('long_logic','—')}（{fact.get('long_logic_score','—')}）")
        if fact.get("h1_note"):
            L.append(f"  - 盈利注：{fact['h1_note']}")
        if fact.get("sw_top_weights"):
            L.append(f"  - 权重股：{'、'.join(fact['sw_top_weights'])}")
        L.append(f"- 成分速览：{top_members(mapj, r['theme'])}")
        L.append(f"- 操作：{TYPE_ACTION[r['type']]}")
        if r["gaps"]:
            L.append(f"- 数据缺口：{'；'.join(r['gaps'])}")
        L.append("")

    L.append("## 十、风险、数据缺口与免责声明")
    L.append("")
    no_fact = [r["theme"] for r in records if r["fact"] is None]
    fact_unused = [f for f in (factj.get("themes") or {}) if f not in {r["fact_name"] for r in records}]
    L.append("### 数据缺口")
    L.append("")
    if no_fact:
        L.append(f"- 以下 {len(no_fact)} 个主题无基本面事实覆盖 → **DATA_INSUFFICIENT，不出 T120 评级**："
                 + "、".join(no_fact))
        rows = []
        for r in records:
            if r["fact"] is None:
                p = r["parts"]
                rows.append([r["theme"], f1(p["60/120日趋势"]), f1(p["相对强度"]), f1(p["机构资金"]),
                             f1(r["composite"]), r["lifecycle"]])
        L.append("")
        L.append(md_table(["主题", "趋势因子", "相对强度", "机构资金", "短线综合", "生命周期"], rows))
    else:
        L.append("- 全部主题均有基本面事实覆盖。")
    if fact_unused:
        L.append(f"- 事实表主题未匹配到日线主题：{'、'.join(fact_unused) if fact_unused else '（无）'}")
    stale_list = [r["theme"] for r in records
                  if r["fact"] and "未刷新" in str(r["fact"].get("h1_vintage", ""))]
    if stale_list:
        L.append(f"- ⚠ 盈利口径为 2025H1（未刷新，不确定性源）：{'、'.join(stale_list)}")
    null_pe = [r["theme"] for r in records if r["fact"] and r["fact"].get("pe") is None]
    if null_pe:
        L.append(f"- PE 缺失（仅 PB 口径）：{'、'.join(null_pe)}")
    if asof_warn:
        L.append(f"- {asof_warn}")
    L.append("")
    L.append("### 风险提示")
    L.append("")
    L.append("- 60/120 日完整主题级历史序列不可得，趋势持续性因子基于可用窗口（"
             f"{hist_days} 个交易日）计算，窗口拉长后需复核。")
    L.append("- 基本面事实为报告日人工快照（Wind 等外部源），估值与盈利随时间衰减，跨期使用必须先刷新 asof。")
    L.append("- 短线引擎字段（含 trade_action）为趋势/情绪口径，不代表中长线结论；两者冲突时以本报告双轨解构为准。")
    L.append("")
    L.append("### 免责声明")
    L.append("")
    L.append("本报告由脚本按既定规则自动生成，仅供研究参考，不构成任何投资建议。"
             "市场有风险，决策需独立判断，盈亏自负。")
    L.append("")
    return "\n".join(L)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="A股 Sector Structure + T60/T120 中长线布局分析器 V2.0")
    ap.add_argument("date", nargs="?", default=None, help="YYYYMMDD，缺省取最新可用日线")
    args = ap.parse_args()

    if args.date:
        date = args.date
    else:
        files = sorted(glob.glob(CSV_GLOB))
        if not files:
            sys.exit("未找到 theme_scores_v2_*.csv")
        m = re.search(r"(\d{8})\.csv$", files[-1])
        if not m:
            sys.exit(f"文件名无法解析日期: {files[-1]}")
        date = m.group(1)

    day_path = os.path.join(REPORT_DIR, f"theme_scores_v2_{date}.csv")
    if not os.path.exists(day_path):
        sys.exit(f"缺少日线文件: {day_path}")

    df = pd.read_csv(day_path, encoding="utf-8-sig")
    hist = load_history()
    factj = load_fact()
    mapj = load_map()
    hist_days = hist["src_date"].nunique() if not hist.empty else 0

    records = build_records(df, hist, factj)
    md = render_report(date, records, factj, mapj, hist_days)

    out = OUT_PATH.format(date=date)
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)

    type_counts = {}
    for r in records:
        type_counts[r["type"]] = type_counts.get(r["type"], 0) + 1
    grade_counts = {}
    for r in records:
        grade_counts[r["grade"]] = grade_counts.get(r["grade"], 0) + 1

    print(f"[V2.0] 报告日 {date} | 主题 {len(records)} | 历史窗口 {hist_days} 日")
    print(f"  类型分布: " + "  ".join(f"{TYPE_LABEL[k]}={v}" for k, v in sorted(type_counts.items())))
    print(f"  T120 分级: " + "  ".join(f"{k}={v}" for k, v in sorted(grade_counts.items())))
    for r in records:
        if r["grade"] in ("CORE", "ACCUMULATION"):
            print(f"  >> {r['theme']}: LONG={f1(r['long'])} {r['grade']} ({TYPE_LABEL[r['type']]})")
    print(f"输出: {out}")


if __name__ == "__main__":
    main()
