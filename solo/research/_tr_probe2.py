# -*- coding: utf-8 -*-
"""检查 stock_data.db 中与涨停/因子相关的表是否可用于本研究的独立校验"""
import os
import sqlite3

DB = r"D:\mystock\cache_daily\stock_data.db"
conn = sqlite3.connect(DB, timeout=60)
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tabs = [r[0] for r in cur.fetchall()]
print("表数:", len(tabs))
for t in tabs:
    print(" -", t)
conn.close()

CD = r"D:\mystock\cache_daily"
print("\n=== cache_daily 目录 csv ===")
for f in sorted(os.listdir(CD)):
    p = os.path.join(CD, f)
    if os.path.isfile(p):
        print(f" {f}  {os.path.getsize(p)/1e6:.1f}MB")
    else:
        print(f" [{f}]/")
