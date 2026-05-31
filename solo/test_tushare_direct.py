#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试直接从 Tushare 获取数据"""
import sys
import os
import tushare as ts
from dotenv import load_dotenv

# 加载环境变量
DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)
ts.set_token(os.getenv('TUSHARE_TOKEN'))
pro = ts.pro_api()

# 测试获取 20260529 的数据
print("=== 从 Tushare 直接获取 20260529 的数据 ===")
test_codes = ['688082.SH', '600021.SH']

for code in test_codes:
    print(f"\n--- {code} ---")
    df = pro.daily(ts_code=code, start_date='20260422', end_date='20260529')
    print(df)
    if not df.empty:
        print(f"最新日期: {df.iloc[0]['trade_date']}, 涨跌幅: {df.iloc[0]['pct_chg']}%")

print("\n=== 完成 ===")
