"""按一级目录展示每个主题的：成分股数、概念板、代表股（Top N）"""
import json

data = json.load(open("cache_backbone_tushare/theme3_constituents_20260612.json", "r", encoding="utf-8"))

# 按一级目录分组
by_cat = {}
for t in data["themes"]:
    by_cat.setdefault(t["top_category"], []).append(t)

print(f"交易日期: {data['trade_date']}  |  版本: {data['version']}  |  主题数: {len(data['themes'])}  |  去重后股票: {data.get('dedup_count','?')}")
print("=" * 120)

TOP_N = 5  # 每个主题展示前 5 只得分最高的股票

for cat, themes in sorted(by_cat.items()):
    total_in_cat = sum(t["n_stocks"] for t in themes)
    print(f"\n## {cat}  (共 {len(themes)} 个子主题, {total_in_cat} 只成分股)")
    print("-" * 120)
    for t in themes:
        concept = t.get("v2_bridge", {}).get("concept", [])
        industry = t.get("v2_bridge", {}).get("industry", [])
        stocks = sorted(t["stocks"], key=lambda x: -x["score"])[:TOP_N]
        stock_str = "  ".join(
            f"{s['name']}({s['ts_code'][:6]}, {s['score']:.0f})"
            for s in stocks
        ) if stocks else "  (无)"
        print(f"  ● {t['theme_name']:<22} {t['n_stocks']:>4}只 | 概念:{','.join(concept[:4]) or '-':<20} | 行业:{','.join(industry[:3]) or '-':<15}")
        print(f"     龙头中军: {stock_str}")
    print()
