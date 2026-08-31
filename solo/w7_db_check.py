# -*- coding: utf-8 -*-
import sqlite3
conn = sqlite3.connect(r"D:\mystock\cache_daily\stock_data.db")
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("表清单:", tables)
for t in tables:
    try:
        n = conn.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
        cols = [c[1] for c in conn.execute(f"PRAGMA table_info('{t}')").fetchall()]
        print(f"\n[{t}] rows={n}")
        print("  列:", cols[:40])
    except Exception as e:
        print(t, "ERR", e)
conn.close()
