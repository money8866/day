import sqlite3
import os

BASE = r"D:\mystock\solo\cache_backbone_tushare"

for db_name in ["theme_portfolio.db", "theme_trend_sentiment.db"]:
    path = os.path.join(BASE, db_name)
    print(f"\n=== {db_name} ===")
    if not os.path.exists(path):
        print("  NOT EXIST")
        continue
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = c.fetchall()
    print(f"  Tables: {tables}")
    for t in tables:
        tname = t[0]
        c.execute(f"PRAGMA table_info({tname})")
        cols = c.fetchall()
        print(f"  [{tname}] columns: {[(c[1], c[2]) for c in cols]}")
        c.execute(f"SELECT COUNT(*) FROM {tname}")
        print(f"  [{tname}] rows: {c.fetchone()[0]}")
    conn.close()

# 检查 dc_hot.db
dc_path = os.path.join(BASE, "dc_hot.db")
print(f"\n=== dc_hot.db ===")
if os.path.exists(dc_path):
    conn = sqlite3.connect(dc_path)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    print(f"  Tables: {c.fetchall()}")
    conn.close()
else:
    print("  NOT EXIST")
