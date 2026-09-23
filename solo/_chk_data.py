# -*- coding: utf-8 -*-
import sqlite3
c = sqlite3.connect(r"D:\mystock\cache_daily\stock_data.db"); c.row_factory = sqlite3.Row
for t in ("daily_cache", "daily_basic_cache", "index_daily_cache", "stk_factor_pro", "fina_indicator_cache"):
    cols = [r[1] for r in c.execute("PRAGMA table_info(%s)" % t)]
    print("== %s ==" % t)
    print("   cols:", cols[:18])
    dc = "trade_date" if "trade_date" in cols else cols[0]
    try:
        for r in c.execute("SELECT %s d, COUNT(*) n FROM %s GROUP BY %s ORDER BY %s DESC LIMIT 3" % (dc, t, dc, dc)):
            print("   %s  n=%d" % (r["d"], r["n"]))
    except Exception as e:
        print("   ERR", e)
print("\n== 索引表内容(有哪些指数) ==")
cols = [r[1] for r in c.execute("PRAGMA table_info(index_daily_cache)")]
cc = "ts_code" if "ts_code" in cols else cols[0]
for r in c.execute("SELECT DISTINCT %s x FROM index_daily_cache LIMIT 20" % cc):
    print("   ", r["x"])
print("\n== 20260923 是否已有数据 ==")
for t in ("daily_cache", "index_daily_cache"):
    try:
        r = c.execute("SELECT COUNT(*) n FROM %s WHERE trade_date='20260923'" % t).fetchone()
        print("   %s: %d" % (t, r["n"]))
    except Exception as e:
        print("   %s ERR %s" % (t, e))
c.close()
