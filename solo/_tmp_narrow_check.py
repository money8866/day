# -*- coding: utf-8 -*-
"""临时脚本：检查三张窄表的实际覆盖范围"""
import sqlite3
import pandas as pd

DB = r"D:\mystock\cache_daily\stock_data.db"
conn = sqlite3.connect(DB)

for table in ("daily_cache", "daily_basic_cache", "adj_factor_cache"):
    try:
        r = conn.execute(f"SELECT COUNT(*), COUNT(DISTINCT ts_code), MIN(trade_date), MAX(trade_date) FROM {table}").fetchone()
        print(f"{table}: rows={r[0]:,} stocks={r[1]:,} range=({r[2]}, {r[3]})")
    except Exception as e:
        print(f"{table}: ERROR {e}")

# daily_basic_cache 按年行数
print("\ndaily_basic_cache by year:")
try:
    rows = conn.execute(
        "SELECT substr(trade_date,1,4) y, COUNT(*) c, COUNT(DISTINCT trade_date) d "
        "FROM daily_basic_cache GROUP BY y ORDER BY y").fetchall()
    for y, c, d in rows:
        print(f"  {y}: rows={c:,} days={d}")
except Exception as e:
    print("  ERROR", e)

print("\nadj_factor_cache by year:")
try:
    rows = conn.execute(
        "SELECT substr(trade_date,1,4) y, COUNT(*) c, COUNT(DISTINCT trade_date) d "
        "FROM adj_factor_cache GROUP BY y ORDER BY y").fetchall()
    for y, c, d in rows:
        print(f"  {y}: rows={c:,} days={d}")
except Exception as e:
    print("  ERROR", e)

# 最近 5 个交易日单日行数（三表对照）
print("\nrecent days (daily vs basic vs adj):")
days = [r[0] for r in conn.execute(
    "SELECT DISTINCT trade_date FROM daily_cache ORDER BY trade_date DESC LIMIT 5")]
for d in sorted(days):
    c1 = conn.execute("SELECT COUNT(*) FROM daily_cache WHERE trade_date=?", (d,)).fetchone()[0]
    c2 = conn.execute("SELECT COUNT(*) FROM daily_basic_cache WHERE trade_date=?", (d,)).fetchone()[0]
    c3 = conn.execute("SELECT COUNT(*) FROM adj_factor_cache WHERE trade_date=?", (d,)).fetchone()[0]
    print(f"  {d}: daily={c1} basic={c2} adj={c3}")

conn.close()
print("\nDONE")
