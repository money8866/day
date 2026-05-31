# -*- coding: utf-8 -*-
import sqlite3

# Check tdx_index columns
conn = sqlite3.connect('cache_db/tdx_concept.db')
cur = conn.cursor()
cur.execute("PRAGMA table_info(tdx_index)")
cols = [r[1] for r in cur.fetchall()]
print('TDX_INDEX_COLS:', cols)

# Get concept index data
cur.execute("SELECT ts_code, name, idx_type, total_mv, float_mv, idx_count FROM tdx_index WHERE trade_date='20260521' AND idx_type='CONCEPT' ORDER BY total_mv DESC LIMIT 30")
print('\nTOP 30 CONCEPT INDICES BY MKTCAP:')
for row in cur.fetchall():
    print(f'{row[0]}|{row[1]}|{row[2]}|MV:{row[3]:.1f}|FV:{row[4]:.1f}|CNT:{row[5]}')

conn.close()

# Check tdx_daily columns
conn2 = sqlite3.connect('cache_db/tdx_concept.db')
cur2 = conn2.cursor()
cur2.execute("PRAGMA table_info(tdx_daily)")
cols2 = [r[1] for r in cur2.fetchall()]
print('\n\nTDX_DAILY_COLS:', cols2)

# Get concept daily data
cur2.execute("SELECT ts_code, name, pct_chg, close, pre_close FROM tdx_daily WHERE trade_date='20260521' ORDER BY pct_chg DESC LIMIT 30")
print('\nTOP 30 CONCEPT INDICES BY PCT_CHG:')
for row in cur2.fetchall():
    print(f'{row[0]}|{row[1]}|PCT_CHG:{row[2]:.2f}|CLOSE:{row[3]:.2f}|PRE:{row[4]:.2f}')

conn2.close()

# Get concept index details for our stocks - check tdx_member
conn3 = sqlite3.connect('cache_db/tdx_concept.db')
cur3 = conn3.cursor()

# Check tdx_member columns
cur3.execute("PRAGMA table_info(tdx_member)")
cols3 = [r[1] for r in cur3.fetchall()]
print('\n\nTDX_MEMBER_COLS:', cols3)

targets = ['688551.SH', '301183.SZ', '002222.SZ', '002428.SZ', '002975.SZ']
print('\nSTOCKS_IN_TDX_MEMBER:')
for code in targets:
    cur3.execute("SELECT con_code, con_name FROM tdx_member WHERE ts_code=? AND trade_date='20260521'", (code,))
    rows = cur3.fetchall()
    print(f'{code}: {len(rows)} concepts')
    for r in rows:
        print(f'  {r[0]}|{r[1]}')

conn3.close()