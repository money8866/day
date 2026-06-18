import json
from pathlib import Path

path = Path("D:/mystock/solo/report_daily/mainboard_v2_scan.json")
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

print(f"扫描时间: {data['scan_time']}")
print(f"共有 {data['total_count']} 只股票通过过滤\n")
print("=" * 150)
print(f"{'排名':<4}{'代码':<12}{'名称':<10}{'主题':<16}{'评级':<4}{'市值亿':<8}{'20日均亿':<10}{'识别分':<8}{'LPS':<8}{'产业强':<8}{'二波分':<8}{'价值余量':<10}{'5日涨':<8}{'60日涨':<8}{'120日涨':<10}{'涨停':<8}{'热榜':<8}")
print("-" * 150)
for i, r in enumerate(data["data"], 1):
    print(
        f"{i:<4}{r['ts_code']:<12}{r['name']:<10}{r['theme']:<16}{r['rating']:<4}"
        f"{r['market_cap_yi']:<8.0f}{r['avg_amount_20d_yi']:<10.1f}"
        f"{r['recognition_score']:<8.1f}{r['leader_persistence_score']:<8.1f}"
        f"{r['industry_strength']:<8.1f}{r['second_wave_score']:<8.1f}"
        f"{r['value_margin_score']:<10.1f}{r['ret_5']:<8.1f}{r['ret_60']:<8.1f}"
        f"{r['ret_120']:<10.1f}{r['limit_up_count_120']:<8}{r['dc_hot_days_120']:<8}"
    )
print("=" * 150)

print("\n【按评级分布】")
from collections import Counter
rating_counts = Counter(r["rating"] for r in data["data"])
for rating in ["S+", "S", "A", "B", "C"]:
    if rating_counts.get(rating, 0) > 0:
        print(f"  {rating}级: {rating_counts[rating]} 只")

print("\n【按主题分布 Top 10】")
theme_counts = Counter(r["theme"] for r in data["data"])
for theme, count in theme_counts.most_common(10):
    print(f"  {theme}: {count} 只")
