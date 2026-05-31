#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成测试历史数据（模拟过去9个交易日TOP5板块）"""
import json
import os

CACHE_DIR = r'C:\Users\kongx\mystock\dragon\cache'
HISTORY_FILE = os.path.join(CACHE_DIR, 'history_themes.json')

# 模拟过去9个交易日的TOP5板块（包含反复出现的板块）
# 注意：PCB概念、培育钻石、超级电容 出现多次
test_history = [
    {"date": "20260512", "top5": ["PCB概念", "培育钻石", "超级电容", "苹果概念", "富士康概念"]},
    {"date": "20260513", "top5": ["PCB概念", "培育钻石", "苹果概念", "通信服务", "超级电容"]},
    {"date": "20260514", "top5": ["PCB概念", "超级电容", "培育钻石", "5G概念", "苹果概念"]},
    {"date": "20260515", "top5": ["培育钻石", "PCB概念", "超级电容", "苹果概念", "富士康概念"]},
    {"date": "20260516", "top5": ["PCB概念", "超级电容", "培育钻石", "苹果概念", "5G概念"]},
    {"date": "20260519", "top5": ["培育钻石", "PCB概念", "超级电容", "苹果概念", "富士康概念"]},
    {"date": "20260520", "top5": ["PCB概念", "超级电容", "培育钻石", "5G概念", "苹果概念"]},
    {"date": "20260521", "top5": ["PCB概念", "培育钻石", "超级电容", "苹果概念", "通信服务"]},
    {"date": "20260522", "top5": ["培育钻石", "PCB概念", "超级电容", "富士康概念", "苹果概念"]},
]

# 写入文件
os.makedirs(CACHE_DIR, exist_ok=True)
with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
    json.dump(test_history, f, ensure_ascii=False, indent=2)

print('SUCCESS: 测试历史数据已生成')
print('  文件路径: %s' % HISTORY_FILE)
print('  交易日数: %d' % len(test_history))
print('')
print('统计：')
from collections import Counter
theme_count = Counter()
for day in test_history:
    theme_count.update(day['top5'])
for name, cnt in theme_count.most_common(10):
    if cnt >= 3:
        print('  %s: %d次 (将获得+10加分)' % (name, cnt))
