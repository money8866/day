#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证亨通光电是否能匹配到主题"""

import sys
sys.path.append('d:/mystock/solo')

from tushare_quant import pro
from theme_trend_sentiment_score import get_dc_members, match_theme_stocks
import json, os

# 加载theme.json
theme_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'theme.json')
with open(theme_path, 'r', encoding='utf-8') as f:
    hot_themes = json.load(f)['HOT_THEMES']

# 获取数据
dc_df = get_dc_members()
try:
    stock_basic_df = pro.stock_basic(fields='ts_code,industry,name')
except:
    stock_basic_df = None

# 匹配
theme_stock_map, name_map_basic, _, _ = match_theme_stocks(hot_themes, dc_df, stock_basic_df)

# 检查亨通光电
ts_code = '600487.SH'
print(f"亨通光电 ({ts_code}):")
found = False
for theme_name, stocks in theme_stock_map.items():
    if ts_code in stocks:
        meta = stocks[ts_code]
        print(f"  ✅ 匹配主题: {theme_name}")
        print(f"     via: {meta.get('via', '')}")
        print(f"     chain_distance: {meta.get('chain_distance', '')}")
        print(f"     score: {meta.get('score', '')}")
        found = True

if not found:
    print("  ❌ 未匹配到任何主题")
    
print(f"\n光通信主题共 {len(theme_stock_map.get('光通信', {}))} 只股票")
