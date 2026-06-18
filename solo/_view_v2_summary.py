import json
from pathlib import Path
from collections import Counter, defaultdict

path = Path("D:/mystock/solo/report_daily/mainboard_v2_scan.json")
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

print(f"总通过数: {data['total_count']}\n")

# 按主题分组
themes = defaultdict(list)
for r in data["data"]:
    themes[r["theme"]].append(r)

print("【主题分布】")
for theme, stocks in sorted(themes.items(), key=lambda x: -len(x[1])):
    names = ", ".join(s["name"] for s in stocks)
    print(f"  {theme}: {len(stocks)} 只 → {names}")

print("\n【全名单按终极分排序】")
print(f"{'排名':<4}{'代码':<12}{'名称':<10}{'主题':<18}{'评级':<4}{'终极分':<8}{'价值':<8}{'产业强':<8}{'60日涨':<10}{'120日涨':<10}{'市值亿':<10}")
print("-" * 115)
for i, r in enumerate(data["data"], 1):
    print(
        f"{i:<4}{r['ts_code']:<12}{r['name']:<10}{r['theme']:<18}{r['rating']:<4}"
        f"{r['ultimate_score']:<8.1f}{r['value_margin_score']:<8.1f}{r['industry_strength']:<8.1f}"
        f"{r['ret_60']:<10.1f}{r['ret_120']:<10.1f}{r['market_cap_yi']:<10.0f}"
    )

# 看看是否有半导体之外的
print("\n\n【非半导体产业链股票】")
semiconductor_keywords = ["PCB", "半导体", "芯片", "先进封装", "IC", "存储", "被动元件", "光学光电", "电子"]
non_semi = []
for r in data["data"]:
    if not any(kw in r["theme"] for kw in semiconductor_keywords):
        non_semi.append(r)

if non_semi:
    for r in non_semi:
        print(f"  {r['name']} ({r['ts_code']}) - {r['theme']} - 终极分{r['ultimate_score']:.1f} - 价值分{r['value_margin_score']:.1f} - 60日涨{r['ret_60']:.1f}%")
else:
    print("  (无 - 全部为半导体产业链相关)")

# 检查一下评分明细，看"theme_score"的分布
print("\n【各主题的theme_score范围】")
theme_scores = defaultdict(list)
for r in data["data"]:
    theme_scores[r["theme"]].append(r["theme_score"])
for theme, scores in sorted(theme_scores.items(), key=lambda x: -sum(x[1])/len(x[1])):
    avg = sum(scores) / len(scores)
    print(f"  {theme}: theme_score 平均={avg:.1f}, 范围=[{min(scores):.1f}, {max(scores):.1f}]")
