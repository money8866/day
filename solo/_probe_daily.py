# -*- coding: utf-8 -*-
"""daily_cache 覆盖范围（临时脚本）"""
import os
import sqlite3

DB = os.path.join(os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily"), "stock_data.db")
con = sqlite3.connect(DB, timeout=60)
print("daily:", con.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date), "
                           "COUNT(DISTINCT ts_code) FROM daily_cache").fetchone())
print("adj  :", con.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM adj_factor_cache").fetchone())
print("daily<20210101:", con.execute("SELECT COUNT(*) FROM daily_cache WHERE trade_date<'20210101'").fetchone())
con.close()
