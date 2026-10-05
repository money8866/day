# -*- coding: utf-8 -*-
"""探针4: daily_basic 覆盖度 / 中证2000 指数样本 / 股票池基础信息"""
import sqlite3
import pandas as pd

DB = r"D:\mystock\cache_daily\stock_data.db"
c = sqlite3.connect(DB)

df = pd.read_sql("SELECT trade_date, COUNT(*) n, COUNT(total_mv) nmv FROM daily_basic_cache "
                 "GROUP BY trade_date", c)
print("daily_basic days:", len(df))
print(df["n"].describe())
print("mv non-null:", df["nmv"].describe())
print("早期:", df.head(3).to_string(index=False))
print("近期:", df.tail(3).to_string(index=False))

d2 = pd.read_sql("SELECT trade_date, COUNT(*) n FROM daily_cache GROUP BY trade_date", c)
print("\ndaily days:", len(d2), d2["n"].describe().to_dict())

idx = pd.read_sql("SELECT * FROM index_daily_cache WHERE ts_code='932000.CSI' "
                  "ORDER BY trade_date DESC LIMIT 3", c)
print("\n932000 tail:\n", idx.to_string(index=False))

mm = pd.read_sql("SELECT MIN(trade_date), MAX(trade_date) FROM index_daily_cache WHERE ts_code='932000.CSI'", c)
print("932000 range:", mm.values)

# 北交所占比
bj = pd.read_sql("SELECT COUNT(*) FROM daily_cache WHERE ts_code LIKE '%.BJ'", c)
print("BJ rows:", bj.values)
