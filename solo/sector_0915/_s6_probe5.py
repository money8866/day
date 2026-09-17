# -*- coding: utf-8 -*-
"""探针：确认 daily_basic_cache.turnover_rate 单位与分位（只读）。"""
import sqlite3

import pandas as pd

DB = r"D:\mystock\cache_daily\stock_data.db"
con = sqlite3.connect(DB)
q = ("SELECT b.trade_date, b.ts_code, b.turnover_rate, b.volume_ratio, p.amount "
     "FROM daily_basic_cache b JOIN daily_cache p "
     "ON b.ts_code = p.ts_code AND b.trade_date = p.trade_date "
     "WHERE b.trade_date = '20260916' AND b.turnover_rate IS NOT NULL")
df = pd.read_sql_query(q, con)
con.close()
print("rows:", len(df))
print(df["turnover_rate"].describe(percentiles=[0.5, 0.9, 0.99]).to_string())
print("---volume_ratio---")
print(df["volume_ratio"].describe(percentiles=[0.5, 0.9, 0.99]).to_string())
print("---amount(千元)---")
print(df["amount"].describe(percentiles=[0.5, 0.9, 0.99]).to_string())
