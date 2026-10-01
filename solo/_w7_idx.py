# -*- coding: utf-8 -*-
"""检查 cache 库的索引与行数（只读，轻量）。"""
import sqlite3
import os

DB = r"D:\mystock\cache_daily\stock_data.db"
print("db size = %.1f MB" % (os.path.getsize(DB) / 1024 / 1024))
con = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
print("\n-- 表 --")
for name, sql in con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"):
    print("  %s" % name)
print("\n-- 索引 --")
for name, tbl, sql in con.execute(
        "SELECT name, tbl_name, sql FROM sqlite_master WHERE type='index' ORDER BY tbl_name, name"):
    print("  tbl=%s  idx=%s  sql=%s" % (tbl, name, (sql or "").replace("\n", " ")[:120]))
print("\n-- 行数 --")
for t in ("daily_cache", "daily_basic_cache", "adj_factor_cache", "fina_indicator_cache"):
    try:
        n = con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        print("  %-24s %d" % (t, n))
    except Exception as exc:
        print("  %-24s 读取失败 %r" % (t, exc))
con.close()
