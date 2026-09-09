# -*- coding: utf-8 -*-
"""临时: 核验 000560.SZ 在 20260820 是否通过 W7 universe 门槛 (用完即删)"""
import sqlite3
import pandas as pd

MIN_CIRC_MV = 500_000.0  # 万元 = 50 亿
conn = sqlite3.connect(r"D:\mystock\cache_daily\stock_data.db")
row = conn.execute(
    "SELECT trade_date, ts_code, turnover_rate, circ_mv, total_mv "
    "FROM daily_basic_cache WHERE ts_code=? AND trade_date=?",
    ("000560.SZ", "20260820")).fetchone()
print("daily_basic 20260820:", row)
if row:
    circ_mv = row[3]
    print(f"circ_mv={circ_mv:.0f}万元 = {circ_mv/10000:.2f}亿 | 门槛50亿 → "
          f"{'通过(可进universe)' if circ_mv >= MIN_CIRC_MV else '不通过(universe外)'}")
# universe 同款条件检查该日在库全市场是否包含 000560
df = pd.read_sql_query(
    "SELECT ts_code, circ_mv FROM daily_basic_cache WHERE trade_date=? "
    "AND ts_code ENDS NOT LIKE '%'", conn, params=("20260820",)) if False else pd.read_sql_query(
    "SELECT ts_code, circ_mv FROM daily_basic_cache WHERE trade_date=?",
    conn, params=("20260820",))
df = df[df.ts_code.str.endswith((".SZ", ".SH"), na=False)]
df = df[~df.ts_code.str.startswith(("8", "43", "83", "87", "92"), na=False)]
df = df[df.circ_mv.fillna(0) >= MIN_CIRC_MV]
print("universe 同款过滤后行数:", len(df), "| 含 000560:", "000560.SZ" in set(df.ts_code))
