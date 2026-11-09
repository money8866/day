# -*- coding: utf-8 -*-
"""检查交易日历与价格表覆盖（临时脚本）"""
import os
import sqlite3

DB = os.path.join(os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily"), "stock_data.db")
con = sqlite3.connect(DB, timeout=60)
print("index_daily 000001.SH:",
      con.execute("SELECT COUNT(DISTINCT trade_date), MIN(trade_date), MAX(trade_date) "
                  "FROM index_daily_cache WHERE ts_code='000001.SH'").fetchone())
print("index_daily 000852.SH:",
      con.execute("SELECT COUNT(DISTINCT trade_date), MIN(trade_date), MAX(trade_date) "
                  "FROM index_daily_cache WHERE ts_code='000852.SH'").fetchone())
print("index 代码数:", con.execute("SELECT COUNT(DISTINCT ts_code) FROM index_daily_cache").fetchone())
print("holder:", con.execute("SELECT COUNT(*), COUNT(DISTINCT end_date) FROM holder_num_cache").fetchone())
con.close()
