# -*- coding: utf-8 -*-
"""检查 daily_basic_cache / stk_factor_pro 覆盖区间与字段"""
import sqlite3
import pandas as pd

DB = r"D:\mystock\cache_daily\stock_data.db"
conn = sqlite3.connect(DB, timeout=60)

for t in ('daily_basic_cache', 'stk_factor_pro', 'factor_snapshot_cache', 'cache_meta'):
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({t})")
    cols = [r[1] for r in cur.fetchall()]
    cur.execute(f"SELECT COUNT(*) FROM {t}")
    cnt = cur.fetchone()[0]
    print(f"=== {t}  行数={cnt}")
    print("   列:", cols)
    if 'trade_date' in cols:
        cur.execute(f"SELECT MIN(trade_date), MAX(trade_date) FROM {t}")
        print("   日期:", cur.fetchone())
        cur.execute(f"SELECT substr(CAST(trade_date AS TEXT),1,4) AS y, COUNT(*) FROM {t} GROUP BY y ORDER BY y")
        print("   分年:", dict(cur.fetchall()))

print("\n=== cache_meta ===")
print(pd.read_sql_query("SELECT * FROM cache_meta LIMIT 40", conn).to_string())
conn.close()
