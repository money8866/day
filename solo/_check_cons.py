# -*- coding: utf-8 -*-
"""检查成份股数据格式"""
import sys, os
sys.path.insert(0, r"d:\mystock\solo")
import pandas as pd
from dotenv import load_dotenv
import tushare as ts

load_dotenv(r"d:/mystock/config/.env")
pro = ts.pro_api(os.getenv("TUSHARE_TOKEN"))

# 检查科创半导体成份股
print("=== 588170.SH index_weight ===")
df1 = pro.index_weight(index_code="588170.SH", start_date="20260430", end_date="20260430")
print(f"行数: {len(df1) if df1 is not None else 0}")
if df1 is not None and not df1.empty:
    print(f"列名: {df1.columns.tolist()}")
    print(df1.head(3))

print("\n=== 562500.SH index_weight ===")
df2 = pro.index_weight(index_code="562500.SH", start_date="20260515", end_date="20260515")
print(f"行数: {len(df2) if df2 is not None else 0}")
if df2 is not None and not df2.empty:
    print(f"列名: {df2.columns.tolist()}")
    print(df2.head(3))

# 试试fund_portfolio
print("\n=== 588170.SH fund_portfolio ===")
df3 = pro.fund_portfolio(ts_code="588170.SH", end_date="20260430")
print(f"行数: {len(df3) if df3 is not None else 0}")
if df3 is not None and not df3.empty:
    print(f"列名: {df3.columns.tolist()}")
    print(df3.head(3))
