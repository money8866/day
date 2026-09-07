# -*- coding: utf-8 -*-
"""临时探查 v7：daily_basic_cache / 有无 adj 相关表 schema、索引、sqlite版本。"""
import sqlite3
DB = r"D:\mystock\cache_daily\stock_data.db"
con = sqlite3.connect(DB)
print("sqlite", sqlite3.sqlite_version)
for t in ("daily_basic_cache", "daily_cache", "adj_factor_cache"):
    r = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
    print(f"\n== {t} 存在={bool(r)}")
    if r:
        for row in con.execute(f"PRAGMA table_info({t})"):
            print("   col:", row[1], row[2], "notnull=", row[3], "pk=", row[5])
        for row in con.execute(f"PRAGMA index_list({t})"):
            print("   index:", row[1], "unique=", row[2])
        row = con.execute(f"SELECT COUNT(*), COUNT(DISTINCT trade_date) FROM {t}").fetchone()
        print("   rows:", row[0], "days:", row[1])
con.close()
