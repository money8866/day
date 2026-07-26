import json
d = json.load(open(r'd:\mystock\cache_daily\theme_stock_map_v2_20260724.json', 'r', encoding='utf-8'))
print('顶层字段:', list(d.keys()))

# 检查 themes 字段
themes = d.get('themes', {})
if isinstance(themes, dict):
    for tn in list(themes.keys())[:3]:
        print(f'\ntheme[{tn}] type={type(themes[tn]).__name__}')
        if isinstance(themes[tn], dict):
            for k, v in list(themes[tn].items())[:5]:
                print(f'  {k}: {str(v)[:80]}')
        elif isinstance(themes[tn], list):
            print(f'  len={len(themes[tn])}, first={str(themes[tn][0])[:80]}')

# 检查 dominant map 里的 theme_scores
dm = d.get('dominant_theme', {})
sample_code = next(iter(dm.keys()))
print(f'\ndominant_theme[{sample_code}]:')
for k, v in dm[sample_code].items():
    print(f'  {k}: {str(v)[:100]}')
