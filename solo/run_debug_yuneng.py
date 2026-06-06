import os
import sys
sys.path.insert(0, '.')
TRADE_DATE = '20260602'

print('=== 调试 ===')

# 先导入主题分析相关模块
from theme_pattern_stock_picker import (
    get_theme_data,
    get_stock_data,
    get_kline_data,
    calculate_and_filter
)

print('1. 加载数据...')
hot_themes, theme_scores = get_theme_data()
stock_basic, daily_basic, theme_stock_map, name_map_basic, mcap_map = get_stock_data(hot_themes)

print('\n2. 检查电力链主题:')
if '电力链' in theme_stock_map:
    print(f'   电力链有 {len(theme_stock_map["电力链"])} 只成分股')
    for code in theme_stock_map['电力链']:
        if '001896' in code:
            print(f'   ✓ 豫能控股: {code}')
            print(f'     名称: {name_map_basic.get(code)}')
            print(f'     市值: {mcap_map.get(code)}亿')

print('\n3. 获取K线...')
all_codes = []
for theme, stock_info in theme_stock_map.items():
    all_codes.extend(list(stock_info.keys()))
all_codes = list(set(all_codes))
kline_data = get_kline_data(all_codes)

print('\n4. 分析所有股票...')
candidates, good_themes = calculate_and_filter(theme_stock_map, kline_data, hot_themes, theme_scores, name_map_basic, mcap_map)

print('\n5. 结果:')
for cand in candidates:
    print(f'  {cand["code"]} {cand["name"]} - {cand["theme_name"]} ({cand["buy_type"]})')
