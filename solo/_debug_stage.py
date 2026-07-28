#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""查看无人机子主题的生命周期阶段"""
import json

with open('d:/mystock/cache_daily/theme_stock_map_v2_20260727.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

rep = data.get('subtheme_report', {})
matrix = rep.get('subtheme_matrix', {})
found = False
for parent, subs in matrix.items():
    for s in subs:
        if '无人机' in s.get('name', ''):
            print(f'母主题: {parent}')
            print(json.dumps(s, ensure_ascii=False, indent=2))
            found = True
if not found:
    print('未在subtheme_matrix中找到无人机')
    for parent, subs in list(matrix.items())[:5]:
        print(f'\n{parent}:')
        names = [s.get('name','') for s in subs[:8]]
        print(f'  {names}')
