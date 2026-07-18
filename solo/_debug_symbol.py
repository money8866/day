# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, r"d:\mystock\solo")
import pandas as pd
from dotenv import load_dotenv
import tushare as ts

load_dotenv(r"d:/mystock/config/.env")
pro = ts.pro_api(os.getenv("TUSHARE_TOKEN"))

df = pro.fund_portfolio(ts_code="588170.SH", end_date="20260430")
print(f"行数: {len(df)}")
print(f"列: {df.columns.tolist()}")
print(f"\n前5个symbol值:")
for i, row in df.head(5).iterrows():
    sym = row['symbol']
    print(f"  [{i}] symbol='{sym}', type={type(sym).__name__}, len={len(str(sym))}")
    
    # 试试能不能拿到数据
    code6 = str(sym).zfill(6)
    if code6.startswith('6'):
        ts_code = f"{code6}.SH"
    elif code6.startswith('0') or code6.startswith('3'):
        ts_code = f"{code6}.SZ"
    else:
        ts_code = f"{code6}.??"
    print(f"       ts_code={ts_code}")
    
    # 试daily
    import time
    daily = pro.daily(ts_code=ts_code, start_date="20260101", end_date="20260430")
    print(f"       daily行数: {len(daily) if daily is not None else 0}")
    time.sleep(0.12)
    if i >= 2:
        break
