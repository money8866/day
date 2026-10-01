# -*- coding: utf-8 -*-
"""定位回放加载慢的根因：SQLite 走哪个索引？哪种写法最快？（只读）"""
import sqlite3
import time
import os

DB = r"D:\mystock\cache_daily\stock_data.db"
con = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)

codes = [r[0] for r in con.execute(
    "SELECT ts_code FROM daily_basic_cache WHERE trade_date='20260904' LIMIT 300")]
ph = ",".join(["?"] * len(codes))
print("样本代码数 = %d" % len(codes))

SEL = """SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close,
       d.pct_chg, d.vol, d.amount,
       b.turnover_rate, b.turnover_rate_f, b.volume_ratio, b.circ_mv,
       a.adj_factor
  FROM daily_cache AS d
  LEFT JOIN daily_basic_cache AS b
         ON b.ts_code = d.ts_code AND b.trade_date = d.trade_date
  LEFT JOIN adj_factor_cache AS a
         ON a.ts_code = d.ts_code AND a.trade_date = d.trade_date"""

varA = SEL + (" WHERE d.trade_date>=? AND d.trade_date<=? AND d.ts_code IN (%s)"
              " ORDER BY d.ts_code, d.trade_date" % ph)
varB = SEL + (" WHERE d.ts_code IN (%s) ORDER BY d.ts_code, d.trade_date" % ph)
varC = ("""SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close,
       d.pct_chg, d.vol, d.amount, a.adj_factor
  FROM daily_cache AS d
  LEFT JOIN adj_factor_cache AS a
         ON a.ts_code = d.ts_code AND a.trade_date = d.trade_date
 WHERE d.ts_code IN (%s) ORDER BY d.ts_code, d.trade_date""" % ph)

for name, q, params in (
        ("A 原写法(带日期范围+不带 basic 表)", varC, list(codes)),
        ("B 仅代码 IN（日期留到 pandas 过滤）", varB, list(codes)),
        ("C 原写法 A（日期范围 + 三表）", varA, ["20230103", "20260904"] + list(codes))):
    print("\n===== %s" % name)
    plan = con.execute("EXPLAIN QUERY PLAN " + q, params).fetchall()
    for row in plan:
        print("   PLAN:", row[3])
    t0 = time.time()
    n = 0
    for _ in con.execute(q, params):
        n += 1
    print("   行数=%d 耗时=%.1fs" % (n, time.time() - t0))

con.close()
