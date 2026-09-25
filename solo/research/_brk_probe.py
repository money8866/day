# -*- coding: utf-8 -*-
"""突破前 Alpha 研究 · 数据勘探（只读）"""
import os
import sqlite3

DB = r"D:\mystock\cache_daily\stock_data.db"
CD = r"D:\mystock\cache_daily"

c = sqlite3.connect(DB, timeout=120)
tabs = [r[0] for r in c.execute("select name from sqlite_master where type='table'")]
print("=== tables ===")
for t in tabs:
    cols = [d[1] for d in c.execute("pragma table_info(%s)" % t)]
    n = c.execute("select count(*) from %s" % t).fetchone()[0]
    print("%-26s rows=%-10d cols=%s" % (t, n, cols))

print("\n=== daily_basic 覆盖 ===")
try:
    for t in tabs:
        cols = [d[1] for d in c.execute("pragma table_info(%s)" % t)]
        if 'turnover_rate' in cols or 'total_mv' in cols:
            r = c.execute("select min(trade_date), max(trade_date), count(*) from %s" % t).fetchone()
            print(t, r)
            r2 = c.execute("select substr(trade_date,1,4) y, count(*) from %s group by y order by y" % t).fetchall()
            print("   逐年:", r2)
except Exception as e:
    print("err", e)

print("\n=== index_daily_cache ===")
for r in c.execute("select ts_code, min(trade_date), max(trade_date), count(*) from index_daily_cache group by ts_code order by count(*) desc limit 15"):
    print(r)
c.close()

print("\n=== cache_daily 目录 ===")
for f in sorted(os.listdir(CD)):
    p = os.path.join(CD, f)
    if os.path.isfile(p):
        print("%-40s %8.1f MB" % (f, os.path.getsize(p) / 1e6))
    else:
        print("%-40s <dir>" % f)

print("\n=== research/out 目录 ===")
O = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')
for f in sorted(os.listdir(O)):
    p = os.path.join(O, f)
    print("%-44s %8.1f MB" % (f, os.path.getsize(p) / 1e6))
