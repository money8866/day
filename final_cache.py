# -*- coding: utf-8 -*-
import sqlite3

targets = ['688551.SH', '301183.SZ', '002222.SZ', '002428.SZ', '002975.SZ']

# Check ALL concept relationship tables
conn = sqlite3.connect('cache_db/tdx_concept.db')
cur = conn.cursor()

# Find all tables with concept relationships
tables = ['dc_concept_cons']
for t in tables:
    print(f'\n=== TABLE: {t} ===')
    # Count by stock
    for code in targets:
        cur.execute(f"SELECT COUNT(*) FROM [{t}] WHERE ts_code=?", (code,))
        cnt = cur.fetchall()
        print(f'{code}: {cnt}')

    # Get all concept names for our stocks
    for code in targets:
        cur.execute(f"""
            SELECT concept_code, concept_name, stock_name, in_date
            FROM [{t}]
            WHERE ts_code=? AND trade_date='20260521'
        """, (code,))
        rows = cur.fetchall()
        if len(rows) > 0:
            print(f'\n  {code} concepts:')
            for r in rows:
                print(f'    {r[0]}|{r[1]}|{r[2]}')

conn.close()

# Get concept index daily (sector/industry indices) data
conn2 = sqlite3.connect('cache_db/tdx_concept.db')
cur2 = conn2.cursor()

# Get concept index performance for 0521
cur2.execute("SELECT ts_code, name, change, pre_close, close, hot, idx_type FROM tdx_index WHERE trade_date='20260521' AND idx_type='CONCEPT' ORDER BY change DESC LIMIT 30")
print('\n\n=== TOP 30 CONCEPT INDICES (20260521) ===')
print('CODE|NAME|CHANGE|PRE_CLOSE|CLOSE|HOT|IDX_TYPE')
for row in cur2.fetchall():
    print(f'{row[0]}|{row[1]}|{row[2]:.2f}|{row[3]:.2f}|{row[4]:.2f}|{row[5]}|{row[6]}')

conn2.close()

# Also get tdx_daily for concept indices to see pct_change
conn3 = sqlite3.connect('cache_db/tdx_concept.db')
cur3 = conn3.cursor()

# Check tdx_daily columns
cur3.execute("PRAGMA table_info(tdx_daily)")
cols = [r[1] for r in cur3.fetchall()]
print(f'\n\nTDX_DAILY_COLS: {cols}')

# Get concept index daily data
cur3.execute("SELECT ts_code, name, change, pct_chg FROM tdx_daily WHERE trade_date='20260521' ORDER BY pct_chg DESC LIMIT 30")
print('\n=== TOP 30 CONCEPT INDICES BY PCT_CHG ===')
for row in cur3.fetchall():
    print(f'{row[0]}|{row[1]}|{row[2]:.2f}|{row[3]:.2f}')

conn3.close()