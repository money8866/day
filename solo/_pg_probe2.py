# -*- coding: utf-8 -*-
"""探针2: 成分股可得性 + daily_cache 覆盖区间 + Tushare 接口可用性"""
import os
import sqlite3

DB = r"D:\mystock\cache_daily\stock_data.db"
c = sqlite3.connect(DB)

r = c.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM daily_cache").fetchone()
print("daily_cache range:", r)
r = c.execute("SELECT trade_date, COUNT(*) FROM daily_cache GROUP BY trade_date "
              "ORDER BY trade_date LIMIT 3").fetchall()
print("daily_cache head:", r)
r = c.execute("SELECT trade_date, COUNT(*) FROM daily_cache GROUP BY trade_date "
              "ORDER BY trade_date DESC LIMIT 3").fetchall()
print("daily_cache tail:", r)

try:
    from dotenv import load_dotenv
    load_dotenv(r"D:\mystock\config\.env")
except Exception as e:
    print("dotenv err", e)
print("TOKEN set:", bool(os.getenv("TUSHARE_TOKEN")))

import tushare as ts
pro = ts.pro_api()
for fn, kw in [("index_member_all", dict(index_code="932000.CSI")),
               ("index_weight", dict(index_code="932000.CSI", start_date="20260901", end_date="20260930")),
               ("index_daily", dict(ts_code="000985.CSI", start_date="20210101", end_date="20260930"))]:
    try:
        df = getattr(pro, fn)(**kw)
        print(f"--- {fn} OK rows={len(df)}")
        print(df.head(3).to_string())
        print("cols:", list(df.columns))
    except Exception as e:
        print(f"--- {fn} FAIL: {e}")
