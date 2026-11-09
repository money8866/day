# -*- coding: utf-8 -*-
"""轮询股东户数落库进度（临时脚本）"""
import os
import sqlite3
import time

DB = os.path.join(os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily"), "stock_data.db")
for _ in range(30):
    try:
        con = sqlite3.connect(DB, timeout=60)
        n, q = con.execute("SELECT COUNT(*), COUNT(DISTINCT end_date) FROM holder_num_cache").fetchone()
        rows = con.execute("SELECT end_date, COUNT(*) FROM holder_num_cache "
                           "GROUP BY end_date ORDER BY end_date").fetchall()
        con.close()
        print("行 %d，季度 %d" % (n, q))
        print(rows[-6:])
        break
    except sqlite3.OperationalError as e:
        print("锁定，等待… %s" % e)
        time.sleep(20)
