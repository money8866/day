import json

with open('d:/mystock/solo/cache_backbone_tushare/trend_lifecycle_v11_20260615.json', 'r', encoding='utf-8') as f:
    d = json.load(f)

print("【爆发分 Top5】")
btop5 = d.get('breakout_themes_top5', [])
for i, b in enumerate(btop5):
    theme = b.get('top_category', '?')
    score = b.get('breakout_score', 0)
    stocks = b.get('top_breakout_stocks', [])
    stocks_str = ', '.join([s.get('name', '?') for s in stocks[:3]]) if stocks else '?'
    print(f"{i+1}. {theme} ({score:.1f}分) - {stocks_str}")

print("\n【结论】")
print(d.get('conclusion', ''))

# 主线详情
primary_cat = d.get('primary_mainline_category', '')
print(f"\n主线: {primary_cat}")

# 从 all_theme_ranking 获取主线详情
ranks = d.get('all_theme_ranking', [])
mainline_list = [r for r in ranks if r.get('theme') == primary_cat]
if mainline_list:
    m = mainline_list[0]
    print(f"主线分: {m.get('mainline_score')} | 中期分: {m.get('mid_term_score')} | 60日涨幅: {m.get('avg_change60_pct')}%")
    print(f"5日均: {m.get('avg_change5_pct')}% | MA60站上: {m.get('above_ma60_ratio')}%")
