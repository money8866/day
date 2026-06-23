# -*- coding: utf-8 -*-
import json

d = json.load(open(r'D:\mystock\cache_daily\theme_stock_map_latest.json', 'r', encoding='utf-8'))

# 正确代码
target_codes = ['603256', '688025']

print("Searching for codes:", target_codes)
for theme_name, stocks in d['themes'].items():
    found = [(s['code'], s['name']) for s in stocks if s['code'].split('.')[0] in target_codes]
    if found:
        print(f"[by code] {theme_name}: {found}")

# 也按名称搜
target_names = ['宏和科技', '杰普特']
for theme_name, stocks in d['themes'].items():
    found = [(s['code'], s['name']) for s in stocks if s['name'] in target_names]
    if found:
        print(f"[by name] {theme_name}: {found}")
