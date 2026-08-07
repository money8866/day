# -*- coding: utf-8 -*-
"""统计中报优质股池(enhanced_timing_bull_all)与主题股池重合度"""
import pandas as pd, json, os

df = pd.read_csv('d:/mystock/solo/report_daily/enhanced_timing_bull_all_20260806.csv', encoding='utf-8-sig')
csv_codes = set(df['代码'].str.strip())
with open('d:/mystock/cache_daily/theme_stock_map_latest.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
theme_codes = set()
for t, stocks in data.get('themes', {}).items():
    for s in stocks:
        c = s.get('code')
        if c:
            theme_codes.add(c)
overlap = csv_codes & theme_codes
print(f'中报股池: {len(csv_codes)} 只')
print(f'主题股池: {len(theme_codes)} 只')
print(f'重合: {len(overlap)} 只')
print(f'中报股池不在主题内: {len(csv_codes - theme_codes)} 只')
# 精选池 (push逻辑): S/A + 洗盘修复分>=80 + 无兑现冲击
sel = df[(df['修正后胜率分级'].isin(['S', 'A'])) & (df['洗盘修复分'] >= 80) & (df['兑现冲击过滤'].str.contains('✅', na=False))]
print(f'精选池(S/A+修复>=80+无冲击): {len(sel)} 只')
print(f'精选池在主题内: {len(set(sel["代码"]) & theme_codes)} 只')
print(f'精选池不在主题内: {len(set(sel["代码"]) - theme_codes)} 只')
sel2 = sel[sel['回踩确认'].str.contains('✅', na=False)]
print(f'再加回踩确认: {len(sel2)} 只')
# 预览精选池
print()
print('精选池名单(前15):')
print(sel[['代码', '名称', '修正后胜率分级', '洗盘修复分', '结构增强分', '回踩确认', '推荐买点类型']].head(15).to_string())
