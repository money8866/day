# -*- coding: utf-8 -*-
import sqlite3
import pickle

targets = ['688551.SH', '301183.SZ', '002222.SZ', '002428.SZ', '002975.SZ']

# Historical concept relationships from the 15万 table
conn = sqlite3.connect('cache_db/tdx_concept.db')
cur = conn.cursor()

print('=== TDX 15K CONCEPT TABLE ===')
for code in targets:
    cur.execute("SELECT concept_code, concept_name, stock_name, in_date FROM dc_concept_cons WHERE ts_code=? AND trade_date='20260521' ORDER BY concept_name", (code,))
    rows = cur.fetchall()
    print(f'STOCK:{code} CONCEPTS:{len(rows)}')
    for r in rows:
        print(f'  {r[0]}|{r[1]}|{r[2]}|IN:{r[3]}')

conn.close()

# dc_concept - top strength by date
conn2 = sqlite3.connect('cache_db/dc_concept.db')
cur2 = conn2.cursor()

# Check what dates exist in dc_concept
cur2.execute("SELECT DISTINCT trade_date FROM dc_concept ORDER BY trade_date DESC LIMIT 10")
dates = [r[0] for r in cur2.fetchall()]
print(f'\nDATES_IN_DC_CONCEPT: {dates}')

# Get today's (latest) concept data
if dates:
    latest = dates[0]
    print(f'\nTOP30_CONCEPTS_DATE:{latest}')
    cur2.execute("SELECT theme_code, name, pct_change, hot, sort, strength FROM dc_concept WHERE trade_date=? ORDER BY strength DESC LIMIT 30", (latest,))
    for row in cur2.fetchall():
        print(f'STR|{row[0]}|{row[1]}|CHG:{row[2]:.2f}|HOT:{row[3]}|STR:{row[5]}')

    # Positive change concepts
    cur2.execute("SELECT theme_code, name, pct_change, hot, strength FROM dc_concept WHERE trade_date=? AND pct_change>0 ORDER BY pct_change DESC LIMIT 15", (latest,))
    print('\nTOP_RISING_CONCEPTS:')
    for row in cur2.fetchall():
        print(f'RIS|{row[0]}|{row[1]}|+{row[2]:.2f}|HOT:{row[3]}|STR:{row[4]}')

    # Our stocks in today's concepts
    print('\nSTOCKS_IN_TODAY_CONCEPTS:')
    for code in targets:
        cur2.execute("SELECT c.theme_code, c.name, c.pct_change, c.hot, c.strength, cc.reason FROM dc_concept_cons cc JOIN dc_concept c ON cc.theme_code=c.theme_code WHERE cc.ts_code=? AND cc.trade_date=? ORDER BY c.strength DESC", (code, latest))
        rows = cur2.fetchall()
        print(f'STOCK:{code} CONCEPTS:{len(rows)}')
        for r in rows:
            print(f'  CON|{r[0]}|{r[1]}|CHG:{r[2]:.2f}|HOT:{r[3]}|STR:{r[4]}|{str(r[5])[:50]}')

conn2.close()

# Also check if there's concept hot ranking by date in tdx_index
conn3 = sqlite3.connect('cache_db/tdx_concept.db')
cur3 = conn3.cursor()
cur3.execute("SELECT DISTINCT trade_date FROM tdx_index ORDER BY trade_date DESC LIMIT 5")
dates2 = [r[0] for r in cur3.fetchall()]
print(f'\nDATES_IN_TDX_INDEX: {dates2}')

if dates2:
    latest2 = dates2[0]
    # Get column names
    cur3.execute(f"PRAGMA table_info(tdx_index)")
    cols = [r[1] for r in cur3.fetchall()]
    print(f'TDX_INDEX_COLS: {cols}')

    cur3.execute(f"SELECT * FROM tdx_index WHERE trade_date=? LIMIT 1", (latest2,))
    sample = cur3.fetchone()
    if sample:
        print(f'TDX_INDEX_SAMPLE: {sample}')

conn3.close()