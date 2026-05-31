#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调试 sector_strength 返回空的问题"""
import sys
sys.path.insert(0, '.')
from sector_strength import analyze_sector_strength
from config import SECTOR

# 检查配置
print('SECTOR config:')
print(f'  top_concepts = {SECTOR.get("top_concepts", "NOT SET")}')
print(f'  top_industries = {SECTOR.get("top_industries", "NOT SET")}')

# 调用分析
print()
print('调用 analyze_sector_strength(trade_date="20260523")...')
result = analyze_sector_strength(trade_date='20260523')

concepts = result.get('concepts')
industries = result.get('industries')
main_themes = result.get('main_themes', [])

print(f'concepts 类型: {type(concepts)}')
if concepts is not None:
    print(f'concepts 行数: {len(concepts)}')
print(f'industries 类型: {type(industries)}')
if industries is not None:
    print(f'industries 行数: {len(industries)}')
print(f'main_themes 数量: {len(main_themes)}')

if main_themes:
    print('TOP5板块:')
    for t in main_themes[:5]:
        print(f'  {t["name"]} (排名{t["rank"]})')
else:
    print('⚠️ main_themes 为空！')

# 检查 concepts 是否返回了数据
if concepts is not None and len(concepts) > 0:
    print()
    print('概念板块TOP5:')
    for i, row in concepts.head().iterrows():
        print(f'  {i+1}. {row.get("name", "N/A")} 涨幅:{row.get("pct_change", 0):.2f}%')
