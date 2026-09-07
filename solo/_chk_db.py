# -*- coding: utf-8 -*-
import sqlite3
c = sqlite3.connect(r"D:\mystock\cache_daily\stock_data.db")
tabs = [r[0] for r in c.execute(
    "select name from sqlite_master where type='table'").fetchall()]
print("TABLES:", tabs)
for t in tabs:
    cols = [r[1] for r in c.execute(f"pragma table_info({t})").fetchall()]
    try:
        n = c.execute(f"select count(*) from {t}").fetchone()[0]
    except Exception:
        n = -1
    print(f"\n== {t} ({n} rows) ==")
    print("  cols:", cols)
c.close()
