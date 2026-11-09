# -*- coding: utf-8 -*-
"""看两张因子表的列与时间覆盖（临时脚本）"""
import os
import sqlite3

DB = os.path.join(os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily"), "stock_data.db")
con = sqlite3.connect(DB, timeout=60)
for t in ("daily_basic_cache", "stk_factor_pro", "index_member_cache", "factor_snapshot_cache"):
    print("\n== %s ==" % t)
    cols = [r[1] for r in con.execute('PRAGMA table_info("%s")' % t).fetchall()]
    print("列(%d): %s" % (len(cols), ",".join(cols)))
    try:
        a, b = con.execute('SELECT MIN(trade_date), MAX(trade_date) FROM "%s"' % t).fetchone()
        print("日期: %s ~ %s" % (a, b))
    except Exception as e:
        print("无 trade_date 列: %s" % e)
con.close()
