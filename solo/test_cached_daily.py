#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试主程序中的数据获取"""
import sys
import os
import tushare as ts

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from theme_rotation_analysis_final import pro, cached_daily

print("=== 测试 cached_daily ===")
test_codes = ['688082.SH', '600021.SH']
start_date = '20260422'
end_date = '20260529'

for code in test_codes:
    print(f"\n--- {code} ---")
    df = cached_daily(code, start_date, end_date)
    print(df)
    if not df.empty:
        print(f"最新日期: {df.iloc[0]['trade_date']}, 涨跌幅: {df.iloc[0]['pct_chg']}%")

print("\n=== 直接使用 pro.daily ===")
for code in test_codes:
    print(f"\n--- {code} ---")
    df = pro.daily(ts_code=code, start_date=start_date, end_date=end_date)
    print(df)
    if not df.empty:
        print(f"最新日期: {df.iloc[0]['trade_date']}, 涨跌幅: {df.iloc[0]['pct_chg']}%")
