import json, os

# 1. theme3.json 主题配置结构
with open('d:/mystock/solo/theme3.json', 'r', encoding='utf-8') as f:
    t3 = json.load(f)
flat = t3.get('THEME_FLAT_MAP', {})
cats = t3.get('CATEGORIES', {})

print('=== theme3.json THEME_FLAT_MAP 示例 ===')
for name in ['AI算力芯片', '先进封装', '功率半导体']:
    cfg = flat.get(name)
    if cfg:
        print(f'\n{name}:')
        print(f'  keys: {list(cfg.keys())}')
        print(f'  theme_type: {cfg.get("theme_type")}')
        print(f'  core_semantic: {cfg.get("core_semantic")}')
        print(f'  industry_roles: {cfg.get("industry_roles",{})}')
        print(f'  business_dna_tags: {cfg.get("business_dna_tags",[])}')
        print(f'  industry_soft_constraints: {cfg.get("industry_soft_constraints",{})}')
        print(f'  stock_role_mapping: {cfg.get("stock_role_mapping",{})}')
        print(f'  matching_strategy: {cfg.get("matching_strategy",{})}')

# 2. theme3_constituents 成分股数据结构
cache_path = 'd:/mystock/solo/cache_backbone_tushare/theme3_constituents_20260612.json'
with open(cache_path, 'r', encoding='utf-8') as f:
    data = json.load(f)
print(f'\n=== theme3_constituents ===')
print(f'Keys: {list(data.keys())}')
print(f'Trade date: {data.get("trade_date")}')
print(f'Match summary: {data.get("match_summary")}')

# 看一个主题的股票结构
for theme in data['themes'][:2]:
    print(f'\n主题: {theme.get("theme_name")} ({theme.get("top_category")})')
    if 'stocks' in theme and theme['stocks']:
        s = theme['stocks'][0]
        print(f'  Stock keys: {list(s.keys())}')
        print(f'  Sample: {s}')
