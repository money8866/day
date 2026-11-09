# -*- coding: utf-8 -*-
"""列出数据库所有表及行数（临时脚本）"""
import os
import sqlite3

DB = os.path.join(os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily"), "stock_data.db")
con = sqlite3.connect(DB, timeout=60)
tabs = [r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
for t in tabs:
    try:
        n = con.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
    except Exception as e:
        n = "ERR:%s" % e
    print("%-32s %s" % (t, n))
con.close()
