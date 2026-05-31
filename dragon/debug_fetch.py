#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调试 fetch_all_concept_boards_daily 返回空的问题"""
import sys
sys.path.insert(0, '.')
from data_engine import fetch_all_concept_boards_daily, fetch_all_industry_boards_daily
import pandas as pd

# 测试多个日期
test_dates = ['20260522', '20260521', '20260520', '20260519']

for date in test_dates:
    print(f'=== 测试日期: {date} ===')
    
    # 强制不用缓存
    concepts = fetch_all_concept_boards_daily(trade_date=date, use_cache=False)
    industries = fetch_all_industry_boards_daily(trade_date=date, use_cache=False)
    
    if concepts is not None and len(concepts) > 0:
        print(f'  concepts: {len(concepts)} 行')
        print(f'  前两行:')
        print(concepts.head(2).to_string())
        break
    else:
        print(f'  concepts: 空')
    
    if industries is not None and len(industries) > 0:
        print(f'  industries: {len(industries)} 行')
        break
    else:
        print(f'  industries: 空')
    
    print()

print()
print('=== 调试完成 ===')
