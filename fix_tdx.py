# -*- coding: utf-8 -*-
import sqlite3

conn = sqlite3.connect('cache_db/tdx_concept.db')
cur = conn.cursor()

# Check actual column names
cur.execute("PRAGMA table_info(tdx_daily)")
print('TDX_DAILY_COLS:', [r[1] for r in cur.fetchall()])

# Check idx_type
cur.execute("SELECT DISTINCT idx_type FROM tdx_index WHERE trade_date='20260521'")
types = [r[0] for r in cur.fetchall()]
print('IDX_TYPES:', types)

# Concept indices
cur.execute("""
    SELECT i.ts_code, i.name, d.pct_change, d.close, d.pre_close, d.rise, d.lu_days, d.limit_up_num, d.bm_buy_net
    FROM tdx_index i
    JOIN tdx_daily d ON i.ts_code = d.ts_code AND i.trade_date = d.trade_date
    WHERE i.trade_date='20260521' AND i.idx_type IN ('CONCEPT', 'CONCEPTS')
    ORDER BY d.pct_change DESC
    LIMIT 30
""")
print('\nTOP 30 CONCEPT INDICES:')
for row in cur.fetchall():
    print(f'{row[0]}|{row[1]}|PCT:{row[2]:.2f}|CLOSE:{row[3]:.2f}|PRE:{row[4]:.2f}|RISE:{row[5]}|LU:{row[6]}|LMTUP:{row[7]}|BM:{row[8]}')

# Also check what types are available
conn.close()

# Try to find ALL concept/sector data
conn2 = sqlite3.connect('cache_db/tdx_concept.db')
cur2 = conn2.cursor()

# Just get all concept indices with their daily data
cur2.execute("""
    SELECT i.ts_code, i.name, d.pct_change, d.rise
    FROM tdx_index i
    JOIN tdx_daily d ON i.ts_code = d.ts_code AND i.trade_date = d.trade_date
    WHERE i.trade_date='20260521'
    ORDER BY d.pct_change DESC
    LIMIT 30
""")
print('\nALL_TOP30_INDICES:')
for row in cur2.fetchall():
    print(f'{row[0]}|{row[1]}|PCT:{row[2]:.2f}|RISE:{row[3]}')

conn2.close()

# Our stocks' concept members
conn3 = sqlite3.connect('cache_db/tdx_concept.db')
cur3 = conn3.cursor()

targets = ['688551.SH', '301183.SZ', '002222.SZ', '002428.SZ', '002975.SZ']
print('\nSTOCKS_TDX_CONCEPTS:')
for code in targets:
    cur3.execute("""
        SELECT m.con_code, i.name, d.pct_change
        FROM tdx_member m
        JOIN tdx_index i ON m.con_code = i.ts_code AND m.trade_date = i.trade_date
        JOIN tdx_daily d ON m.con_code = d.ts_code AND m.trade_date = d.trade_date
        WHERE m.ts_code=? AND m.trade_date='20260521'
        ORDER BY d.pct_change DESC
    """, (code,))
    rows = cur3.fetchall()
    print(f'{code} ({len(rows)} concepts):')
    for r in rows[:15]:
        print(f'  {r[0]}|{r[1]}|{r[2]:.2f}%')

conn3.close()