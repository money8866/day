import json
from pathlib import Path

path = Path("D:/mystock/solo/report_daily/mainboard_v2_scan.json")
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

print("=" * 120)
print(f"{'排名':<4}{'代码':<12}{'名称':<10}{'主题':<16}{'评级':<4}{'终极分':<8}{'价值余量':<10}{'历史验证':<10}{'乖离MA20':<12}{'60日涨%':<10}{'120日涨%':<12}{'市值亿':<10}")
print("-" * 120)
for i, r in enumerate(data["data"], 1):
    validated = 0.30 * r["recognition_score"] + 0.25 * r["leader_persistence_score"] + 0.20 * r["theme_score"] + 0.25 * r["bull_score"]
    bias = r.get("bias_ma20", 0)
    color_mark = ""
    if r["value_margin_score"] >= 75:
        color_mark = "★"  # 价值未透支
    elif r["value_margin_score"] >= 60:
        color_mark = "☆"  # 价值尚可
    else:
        color_mark = "⚠"  # 价值透支风险
    print(
        f"{i:<4}{r['ts_code']:<12}{r['name']:<10}{r['theme']:<16}{r['rating']:<4}"
        f"{r['ultimate_score']:<8.1f}{r['value_margin_score']:<10.1f}{validated:<10.1f}"
        f"{bias:<12.1f}{r['ret_60']:<10.1f}{r['ret_120']:<12.1f}{r['market_cap_yi']:<10.0f}{color_mark}"
    )
print("=" * 120)
print()
print("★ 价值未透支(≥75)   ☆ 价值尚可(60~75)   ⚠ 价值透支风险(<60)")
print()
print("【按评级分布】")
from collections import Counter
rating_counts = Counter(r["rating"] for r in data["data"])
for rating in ["S+", "S", "A", "B", "C"]:
    if rating_counts.get(rating, 0) > 0:
        names = [r["name"] for r in data["data"] if r["rating"] == rating]
        print(f"  {rating}级: {rating_counts[rating]} 只 -> {', '.join(names[:10])}{'...' if len(names) > 10 else ''}")

print()
print("【按主题分布 Top 8】")
theme_counts = Counter(r["theme"] for r in data["data"])
for theme, count in theme_counts.most_common(8):
    names = [r["name"] for r in data["data"] if r["theme"] == theme]
    print(f"  {theme}: {count} 只 -> {', '.join(names)}")

print()
print("【价值余量 Top 10（最未被透支的）】")
sorted_vm = sorted(data["data"], key=lambda x: x["value_margin_score"], reverse=True)[:10]
for r in sorted_vm:
    print(f"  {r['name']:<10} {r['theme']:<16} 价值分={r['value_margin_score']:.0f} 60日涨={r['ret_60']:.1f}% 120日涨={r['ret_120']:.1f}%")
