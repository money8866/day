#!/usr/bin/env python
# -*- coding: utf-8 -*-
import tushare as ts
from dotenv import load_dotenv
import os
load_dotenv('d:/mystock/config/.env')
pro = ts.pro_api(os.getenv('TUSHARE_TOKEN'))

df = pro.ths_index(exchange='A', type='N')

print('包含光的概念:')
for _, r in df.iterrows():
    if '光' in r['name']:
        print(f'  {r["name"]}: {r["ts_code"]}')

print()
print('包含通信的概念:')
for _, r in df.iterrows():
    if '通信' in r['name']:
        print(f'  {r["name"]}: {r["ts_code"]}')

print()
print('包含华为的概念:')
for _, r in df.iterrows():
    if '华为' in r['name']:
        print(f'  {r["name"]}: {r["ts_code"]}')

print()
print('包含CPO的概念:')
for _, r in df.iterrows():
    if 'CPO' in r['name']:
        print(f'  {r["name"]}: {r["ts_code"]}')