#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用2026-05-22真实数据测试历史主线跟踪"""
import sys
sys.path.insert(0, '.')
from sector_strength import analyze_sector_strength, track_history_themes, find_recurring_themes
import json

# 1. 获取2026-05-22的板块排行
print('=== 步骤1: 获取2026-05-22板块排行 ===')
result = analyze_sector_strength(trade_date='20260522')
main_themes = result.get('main_themes', [])

# 提取TOP5板块名称
top5_names = [theme['name'] for theme in main_themes[:5]]
print(f'TOP5板块: {top5_names}')

# 2. 记录到历史
print()
print('=== 步骤2: 记录到历史 ===')
history = track_history_themes('20260522', top5_names)
print(f'历史记录条数: {len(history)}')
print('最近3条:')
for h in history[-3:]:
    print(f'  {h["date"]}: {h["top5"]}')

# 3. 查找反复活跃板块
print()
print('=== 步骤3: 查找反复活跃板块 ===')
recurring = find_recurring_themes(history, min_count=2)
if recurring:
    print('反复活跃板块:')
    for r in recurring:
        print(f'  {r["name"]}: 出现{r["count"]}次')
else:
    print('(无，需要更多历史数据)')

# 4. 写入文件（避免PowerShell编码问题）
print()
print('=== 写入结果文件 ===')
with open('test_history_final.txt', 'w', encoding='utf-8') as f:
    f.write('=== 历史主线跟踪测试结果 ===\n\n')
    f.write(f'TOP5板块: {top5_names}\n\n')
    f.write(f'历史记录条数: {len(history)}\n')
    f.write('最近3条:\n')
    for h in history[-3:]:
        f.write(f'  {h["date"]}: {h["top5"]}\n')
    f.write('\n')
    if recurring:
        f.write('反复活跃板块:\n')
        for r in recurring:
            f.write(f'  {r["name"]}: 出现{r["count"]}次\n')
    else:
        f.write('(无，需要更多历史数据)\n')

print('✅ 结果已写入 test_history_final.txt')
print()
print('=== 测试完成 ===')
