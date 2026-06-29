# -*- coding: utf-8 -*-
"""检查PDF源数据"""
import pandas as pd
import re

df = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\output\basic_info_auto_20260629.csv')

print('='*80)
print('原始数据检查')
print('='*80)

for i, (idx, row) in enumerate(df.iterrows(), 1):
    ts_code = row['ts_code']
    weight = row['weight']
    title = str(row['title'])
    
    # 清理HTML标签
    clean_title = re.sub(r'<[^>]+>', '', title)
    
    print(f'{i}. {ts_code} - {weight}分')
    print(f'   原始: {title[:80]}')
    print(f'   清理后: {clean_title[:80]}')
    print()
