import json

with open('d:/mystock/solo/cache_backbone_tushare/trend_lifecycle_v11_20260612.json', 'r', encoding='utf-8') as f:
    d = json.load(f)

ranks = d.get('all_theme_ranking', [])

print(f"{'主题':<12} {'主线分':>6} {'中期分':>6} {'5日均':>6} {'60日均':>7} {'MA60站上':>9} {'分类':<10}")
print("-" * 75)

for r in ranks:
    print(f"{r['theme']:<12} {r['mainline_score']:>6.1f} {r['mid_term_score']:>6.1f} {r['avg_change5_pct']:>6.1f} {r['avg_change60_pct']:>7.1f} {r['above_ma60_ratio']:>9.1f} {r['classification']:<10}")

print(f"\n结论: {d.get('conclusion', '')}")
