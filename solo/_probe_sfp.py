# -*- coding: utf-8 -*-
"""stk_factor_pro 覆盖度（临时脚本）"""
import os
import sqlite3

DB = os.path.join(os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily"), "stock_data.db")
con = sqlite3.connect(DB, timeout=120)
for a, b in (("20200101", "20221231"), ("20230101", "20261106"), ("20200101", "20261106")):
    r = con.execute("SELECT COUNT(*), COUNT(DISTINCT trade_date), COUNT(DISTINCT ts_code) "
                    "FROM stk_factor_pro WHERE trade_date>=? AND trade_date<=?", (a, b)).fetchone()
    print("%s~%s 行 %d 日 %d 股 %d" % (a, b, r[0], r[1], r[2]))
print("adj_factor 非空(2020+):",
      con.execute("SELECT COUNT(*) FROM stk_factor_pro WHERE trade_date>='20200101' "
                  "AND adj_factor IS NOT NULL").fetchone())
print("样本行:", con.execute(
    "SELECT ts_code,trade_date,close,adj_factor,close_qfq FROM stk_factor_pro "
    "WHERE ts_code='600519.SH' AND trade_date IN ('20211231','20230103')").fetchall())
con.close()
