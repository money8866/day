"""
ELD × 主题关联分析
关联 ELD V2 TOP50 选股结果与主题引擎（V8阶段/D阶段/子主题）
"""
import json
import csv
import os
from collections import defaultdict

# ── 配置 ──
ELD_CSV = r"D:\mystock\report_daily\eld_report_20260728.csv"
THEME_STOCK_MAP = r"D:\mystock\cache_daily\theme_stock_map_v2_20260728.json"
V8_RESULT = r"D:\mystock\solo\theme_alpha_v6\cache\theme_alpha_v6_result_v8_20260728.json"
TOP_N = 50

# ── 1. 读取 ELD 数据 ──
eld_stocks = {}
with open(ELD_CSV, encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    for row in reader:
        rank = int(row["rank"])
        if rank > TOP_N:
            break
        eld_stocks[row["ts_code"]] = {
            "rank": rank,
            "name": row["name"],
            "industry": row["industry"],
            "forecast_pct": float(row["forecast_pct"]),
            "final_score_v2": float(row["final_score_v2"]),
            "expectation_gap_v2": float(row["expectation_gap_v2"]),
            "institution_accumulation": float(row["institution_accumulation"]),
            "institution_state": row["institution_state"],
            "earnings_buy_signal": row["earnings_buy_signal"],
        }

print(f"ELD TOP{TOP_N}: {len(eld_stocks)} 只股票")

# ── 2. 读取主题-股票映射 ──
with open(THEME_STOCK_MAP, encoding="utf-8") as f:
    ts_map = json.load(f)

# 构建 {ts_code: [主题名]}
stock_to_themes = defaultdict(list)
for theme_name, stocks in ts_map.get("themes", {}).items():
    for s in stocks:
        code = s.get("code", "")
        if code:
            stock_to_themes[code].append({
                "theme": theme_name,
                "via": s.get("via", ""),
                "score": s.get("score", 0),
            })

# ── 3. 读取 V8 主题分析（生命周期/阶段/D阶段） ──
with open(V8_RESULT, encoding="utf-8") as f:
    v8_themes = json.load(f)

v8_lookup = {}
for t in v8_themes:
    name = t["主题"]
    v8_lookup[name] = t

# ── 4. 关联分析 ──
# 统计 ELD 股票覆盖的主题
theme_eld_map = defaultdict(list)
theme_eld_count = defaultdict(int)
eld_mapped = 0
eld_unmapped = []

for ts_code, stock in eld_stocks.items():
    themes = stock_to_themes.get(ts_code, [])
    if themes:
        eld_mapped += 1
        for t in themes:
            theme_eld_map[t["theme"]].append({**stock, "ts_code": ts_code})
            theme_eld_count[t["theme"]] += 1
    else:
        eld_unmapped.append({**stock, "ts_code": ts_code})

print(f"ELD 股票已匹配主题: {eld_mapped}, 未匹配: {len(eld_unmapped)}")

# ── 5. 输出报告 ──
lines = []
lines.append("# ELD V2 TOP50 × 主题关联分析 — 20260728")
lines.append("")
lines.append(f"> ELD TOP{TOP_N} 股票中，{eld_mapped} 只映射到主题系统，{len(eld_unmapped)} 只无主题匹配")
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 一、主题分布总览（按 ELD 股票数量排序）")
lines.append("")
lines.append("| 主题 | ELD股票数 | 平均V2分 | 平均预期差 | V7阶段 | D阶段 | 策略动作 |")
lines.append("|------|----------|---------|-----------|--------|-------|---------|")
# 按数量排序
sorted_themes = sorted(theme_eld_count.items(), key=lambda x: -x[1])
for tname, cnt in sorted_themes:
    stocks_list = theme_eld_map[tname]
    avg_v2 = sum(s["final_score_v2"] for s in stocks_list) / cnt
    avg_gap = sum(s["expectation_gap_v2"] for s in stocks_list) / cnt
    v8 = v8_lookup.get(tname, {})
    stage = v8.get("V7阶段", "N/A")
    d_stage = v8.get("D阶段", "N/A")
    action = v8.get("策略动作", "")
    lines.append(f"| {tname} | {cnt} | {avg_v2:.1f} | {avg_gap:.0f} | {stage} | {d_stage} | {action} |")

# 未匹配的
if eld_unmapped:
    lines.append(f"| *无主题匹配* | {len(eld_unmapped)} | {sum(s['final_score_v2'] for s in eld_unmapped)/len(eld_unmapped):.1f} | - | - | - | - |")

lines.append("")
lines.append("---")
lines.append("")
lines.append("## 二、主题详情（含子主题/成份股维度）")
lines.append("")

for tname, cnt in sorted_themes:
    stocks_list = theme_eld_map[tname]
    v8 = v8_lookup.get(tname, {})
    stage = v8.get("V7阶段", "N/A")
    d_stage = v8.get("D阶段", "N/A")
    score = v8.get("V7综合得分", "N/A")
    
    lines.append(f"### {cnt}. {tname}")
    lines.append("")
    lines.append(f"- **V7综合得分**: {score} | **V7阶段**: {stage} | **D阶段**: {d_stage}")
    lines.append(f"- **ELD 命中**: {cnt} 只 | **平均V2分**: {sum(s['final_score_v2'] for s in stocks_list)/cnt:.1f}")
    
    # 子主题维度：分析这些股票的共同行业和概念
    lines.append("")
    lines.append("| 排名 | 代码 | 名称 | 行业 | V2分 | 预期差 | 机构状态 | 买点信号 |")
    lines.append("|------|------|------|------|------|--------|----------|---------|")
    for s in sorted(stocks_list, key=lambda x: x["rank"]):
        lines.append(f"| {s['rank']} | {s['ts_code']} | {s['name']} | {s['industry']} "
                     f"| {s['final_score_v2']:.1f} | {s['expectation_gap_v2']:.0f} "
                     f"| {s['institution_state']} | {s['earnings_buy_signal']} |")
    lines.append("")

lines.append("---")
lines.append("")
lines.append("## 三、未匹配主题的 ELD 股票")
lines.append("")
lines.append("这些股票在 theme_stock_map 中无对应主题，可能属于未被主题引擎覆盖的个股机会。")
lines.append("")
lines.append("| 排名 | 代码 | 名称 | 行业 | V2分 | 预期差 | 机构状态 |")
lines.append("|------|------|------|------|------|--------|----------|")
for s in sorted(eld_unmapped, key=lambda x: x["rank"]):
    lines.append(f"| {s['rank']} | {s['ts_code']} | {s['name']} | {s['industry']} "
                 f"| {s['final_score_v2']:.1f} | {s['expectation_gap_v2']:.0f} "
                 f"| {s['institution_state']} |")

lines.append("")
lines.append("---")
lines.append("")
lines.append("## 四、量化总结")
lines.append("")

# 统计不同D阶段的分布
d_stage_counts = defaultdict(int)
for tname, cnt in theme_eld_count.items():
    v8 = v8_lookup.get(tname, {})
    d = v8.get("D阶段", "N/A")
    d_stage_counts[d] += cnt

lines.append("### 4.1 ELD 股票按 D 阶段分布")
lines.append("")
lines.append("| D阶段 | ELD命中数 | 含义 |")
lines.append("|-------|----------|------|")
d_stage_map = {
    "D1-D2": "启动/试错期",
    "D3": "主升确认期",
    "D4-D5": "加速/高潮期",
    "D6-D7": "分歧/震荡期",
    "D8+": "退潮/回避期",
}
for d in ["D1-D2", "D3", "D4-D5", "D6-D7", "D8+"]:
    if d in d_stage_counts:
        lines.append(f"| {d} | {d_stage_counts[d]} | {d_stage_map.get(d, '')} |")
if "N/A" in d_stage_counts:
    lines.append(f"| N/A | {d_stage_counts['N/A']} | 无V8阶段数据 |")

lines.append("")
lines.append("### 4.2 ELD 股票按机构状态分布")
lines.append("")
inst_counts = defaultdict(int)
for ts_code, s in eld_stocks.items():
    inst_counts[s["institution_state"]] += 1
lines.append("| 机构状态 | 数量 | 含义 |")
lines.append("|----------|------|------|")
for state, cnt in sorted(inst_counts.items(), key=lambda x: -x[1]):
    lines.append(f"| {state} | {cnt} | {'主力吸筹中' if state == '吸筹' else '洗盘震荡' if state == '洗盘' else '主力派发' if state == '派发' else ''} |")

lines.append("")
lines.append("### 4.3 ELD 股票按买点信号分布")
lines.append("")
buy_counts = defaultdict(int)
for ts_code, s in eld_stocks.items():
    buy_counts[s["earnings_buy_signal"]] += 1
lines.append("| 买点信号 | 数量 | 含义 |")
lines.append("|----------|------|------|")
for signal, cnt in sorted(buy_counts.items(), key=lambda x: -x[1]):
    lines.append(f"| {signal} | {cnt} | {'买入窗口' if signal == 'BUY' else '关注等待' if signal == 'WATCH' else '忽略/观望' if signal == 'IGNORE' else ''} |")

lines.append("")
lines.append("### 4.4 深度交叉分析：最佳主题机会")
lines.append("")
lines.append("筛选条件：V8排名前15 + D阶段D1-D6 + 主题内有≥2只ELD股票")
lines.append("")

hot_themes = []
for tname, cnt in theme_eld_count.items():
    v8 = v8_lookup.get(tname, {})
    v8_score = v8.get("V7综合得分", 0)
    d_stage = v8.get("D阶段", "N/A")
    if v8_score >= 30 and cnt >= 2 and d_stage not in ("D8+", "N/A"):
        avg_v2 = sum(s["final_score_v2"] for s in theme_eld_map[tname]) / cnt
        hot_themes.append((tname, cnt, v8_score, d_stage, avg_v2))

hot_themes.sort(key=lambda x: -x[2])

lines.append("| 主题 | ELD数 | V7分 | D阶段 | ELD均V2分 | 策略 |")
lines.append("|------|-------|------|-------|----------|------|")
for tname, cnt, v8_score, d_stage, avg_v2 in hot_themes:
    v8 = v8_lookup.get(tname, {})
    action = v8.get("策略动作", "")
    lines.append(f"| {tname} | {cnt} | {v8_score:.1f} | {d_stage} | {avg_v2:.1f} | {action} |")

report = "\n".join(lines)

# ── 保存报告 ──
output_path = r"D:\mystock\report_daily\eld_themes_link_20260728.md"
with open(output_path, "w", encoding="utf-8") as f:
    f.write(report)
print(f"\n报告已保存: {output_path}")
print(f"\n未匹配的股票: {len(eld_unmapped)} 只")
for s in sorted(eld_unmapped, key=lambda x: x["rank"]):
    print(f"  {s['rank']}. {s['ts_code']} {s['name']} ({s['industry']})")
