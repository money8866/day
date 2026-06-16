#!/usr/bin/env python
# -*- coding: utf-8 -*-
import tushare as ts
from dotenv import load_dotenv
import os
load_dotenv('d:/mystock/config/.env')
pro = ts.pro_api(os.getenv('TUSHARE_TOKEN'))

print('申万一级行业:')
df = pro.index_classify(src='SW2021', level='L1')
for _, r in df.iterrows():
    print(f"  {r['industry_name']} ({r['industry_code']})")

print()
print('申万二级行业:')
df = pro.index_classify(src='SW2021', level='L2')
for _, r in df.iterrows():
    print(f"  {r['industry_code']}: {r['industry_name']}")