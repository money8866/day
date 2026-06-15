import json, sqlite3, os

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "cache_backbone_tushare", "cache.db")
conn = sqlite3.connect(DB)
cur = conn.cursor()

# 先看看有哪些表
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print("TABLES:", [r[0] for r in cur.fetchall()])

# 查一下可能的股票映射表结构
for table in ["dc_member_stocks", "member_stocks", "concept_members", "dc_members", "stock_plates"]:
    try:
        cur.execute(f"PRAGMA table_info({table})")
        cols = cur.fetchall()
        if cols:
            print(f"\n[{table}] columns:", [(c[1], c[2]) for c in cols])
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            print(f"  rows: {cur.fetchone()[0]}")
    except Exception as e:
        pass
