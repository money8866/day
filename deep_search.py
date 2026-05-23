# -*- coding: utf-8 -*-
import tushare as ts
import os
os.environ['PYTHONIOENCODING'] = 'utf-8'

ts.set_token('bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d')
pro = ts.pro_api()

# Search all stocks for 威 or 迈
df = pro.stock_basic(fields='ts_code,name,industry,list_date,market')
results = []
for _, row in df.iterrows():
    name = row['name']
    code = row['ts_code']
    if any(kw in name for kw in ['威', '迈', '韦']):
        results.append(f'{code}|{name}|{row["industry"]}|{row["list_date"]}|{row["market"]}')

print(f'Total matches for 威/迈/韦: {len(results)}')
for r in results:
    print(r)

# Also try recent IPOs (2024-2026)
print('\n--- Recent IPOs (2024+) ---')
for _, row in df.iterrows():
    ld = str(row['list_date'])
    if ld >= '20240101':
        name = row['name']
        if len(name) <= 6 and ('斯' in name or '威' in name or '迈' in name or '控' in name or '电' in name or '技' in name):
            print(f'{row["ts_code"]}|{name}|{row["industry"]}|{ld}')