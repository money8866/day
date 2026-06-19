#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查询高盛进入前十大股东的A股个股"""
import sys, time
sys.path.insert(0, r'C:\Users\kongx\AppData\Local\Programs\Python\Python313\Lib\site-packages')
import tushare as ts

ts.set_token('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')
pro = ts.pro_api()

# 查询高盛在Q1 2026的前十大流通股东
# Tushare top10_floatholders 接口
# 先试一个已知股：海目星 688559
code = '688559.SH'
print(f"=== {code} ===")
df = pro.top10_floatholders(ts_code=code, period='20260331')
if df is not None and len(df) > 0:
    for _, r in df.iterrows():
        print(f"  {r.get('float_holder_name','')} | {r.get('hold_amount','')}万股 | {r.get('hold_ratio','')}%")
else:
    print("  无数据")

# 试另一个接口: top10_holders
print(f"\n=== top10_holders ===")
df2 = pro.top10_holders(ts_code=code, period='20260331')
if df2 is not None and len(df2) > 0:
    for _, r in df2.iterrows():
        print(f"  {r.get('holder_name','')} | {r.get('hold_amount','')}万股 | {r.get('hold_ratio','')}%")
else:
    print("  无数据")
