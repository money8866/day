# -*- coding: utf-8 -*-
import sqlite3, os

db_dir = r'C:\Users\kongx\mystock\cache_db'
files = [f for f in os.listdir(db_dir) if f.endswith('.db')]
print(f"DB files: {files[:5]}")

for f in files[:2]:
    path = os.path.join(db_dir, f)
    conn = sqlite3.connect(path)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    print(f"\n{f}: tables={tables}")
    for t in tables[:2]:
        cur2 = conn.execute(f"SELECT * FROM {t} LIMIT 2")
        cols = [d[0] for d in conn.execute(f"PRAGMA table_info({t})").fetchall()]
        rows = cur2.fetchall()
        print(f"  {t}: cols={cols}")
        if rows: print(f"  sample: {rows[0]}")
    conn.close()
