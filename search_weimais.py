# -*- coding: utf-8 -*-
import tushare as ts
import os
os.environ['PYTHONIOENCODING'] = 'utf-8'

ts.set_token('bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d')
pro = ts.pro_api()

# Search by name
df = pro.stock_basic(market='科创板', fields='ts_code,name,industry,list_date')
# find 威迈斯
for _, row in df.iterrows():
    name = row['name']
    if '威迈' in name or '迈斯' in name or '威' in name:
        print(f'{row["ts_code"]}|{name}|{row["industry"]}|{row["list_date"]}')

# Also search in all stocks
df2 = pro.stock_basic(fields='ts_code,name')
for _, row in df2.iterrows():
    if '迈斯' in row['name'] or '威迈' in row['name']:
        print(f'ALL: {row["ts_code"]}|{row["name"]}')

# Try searching via name fuzzy
print('\n--- Searching all for 迈 ---')
for _, row in df2.iterrows():
    if '迈' in row['name']:
        print(f'{row["ts_code"]}|{row["name"]}')