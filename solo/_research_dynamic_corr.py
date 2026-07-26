"""调研：飞龙股份的主题归属和ETF映射"""
import json, sys
sys.path.insert(0, 'd:\\mystock\\solo')

# 1. 加载主题映射
with open(r'cache_daily\theme_stock_map_v2_20260724.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

stock = data['stocks'].get('002536.SZ', {})
print(f'飞龙股份(002536.SZ)')
print(f'  themes: {stock.get("themes",[])}')
print(f'  industry: {stock.get("industry","")}')

# 2. 加载主题配置（找ETF）
with open(r'solo\theme_kg_v3\theme_kg_v3\config\theme_config.json', 'r', encoding='utf-8') as f:
    cfg = json.load(f)

for key, val in cfg.items():
    if key.startswith('_'): continue
    cn = val.get('name_cn','')
    if cn in ('新能源车', 'AI算力', '智能驾驶', '机器人'):
        print(f'\n{cn} ({key}):')
        print(f'  main_etf: {val.get("main_etf","")}')
        print(f'  etf_codes: {val.get("etf_codes",[])}')
        print(f'  keywords: {val.get("keywords",[])[:5]}...')

# 3. Check 飞龙股份 concepts
from theme_trend_sentiment_score import get_dc_members
dc_df = get_dc_members()
if dc_df is not None:
    concepts = dc_df[dc_df['ts_code'] == '002536.SZ']['name'].tolist()
    print(f'\n飞龙股份 东财概念: {concepts}')
