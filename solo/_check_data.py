import json

constituents = json.load(open('cache_backbone_tushare/theme3_constituents_20260612.json', encoding='utf-8'))
data = json.load(open('cache_backbone_tushare/trend_lifecycle_v11_20260612.json', encoding='utf-8'))

all_themes = constituents.get('themes', [])
categories = list(set(t.get('top_category', '其他') for t in all_themes))

print(f"主题配置文件中的顶级主题数: {len(categories)}")
print(f"各主题下的子主题:")
for c in sorted(categories):
    subs = [t.get('theme_name') for t in all_themes if t.get('top_category') == c]
    print(f"  {c}: {len(subs)}个子主题")

bt = data.get('breakout_themes_top5', [])
print(f"\nV11输出的 breakout_themes_top5 数量: {len(bt)}")

ranking = data.get('all_theme_ranking', [])
print(f"V11输出的 all_theme_ranking 数量: {len(ranking)}")
print(f"all_theme_ranking中的主题: {[x['theme'] for x in ranking]}")
