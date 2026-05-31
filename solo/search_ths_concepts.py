#!/usr/bin/env python
# -*- coding: utf-8 -*-
import tushare as ts
from dotenv import load_dotenv
import os
load_dotenv('d:/mystock/config/.env')
pro = ts.pro_api(os.getenv('TUSHARE_TOKEN'))

df = pro.ths_index(exchange='A', type='N')
print('总概念数:', len(df))
print()

print('包含AI的概念:')
for _, r in df.iterrows():
    if 'AI' in r['name']:
        print(f'  {r["name"]}: {r["ts_code"]}')

print()
print('包含芯片的概念:')
for _, r in df.iterrows():
    if '芯片' in r['name']:
        print(f'  {r["name"]}: {r["ts_code"]}')

print()
print('包含光模块的概念:')
for _, r in df.iterrows():
    if '光模块' in r['name']:
        print(f'  {r["name"]}: {r["ts_code"]}')