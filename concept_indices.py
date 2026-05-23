# -*- coding: utf-8 -*-
import sqlite3

conn = sqlite3.connect('cache_db/tdx_concept.db')
cur = conn.cursor()

# Check idx_type values
cur.execute("SELECT DISTINCT idx_type FROM tdx_index WHERE trade_date='20260521'")
print('IDX_TYPES:', [r[0] for r in cur.fetchall()])

# Concept indices - join with tdx_daily for performance
cur.execute("""
    SELECT i.ts_code, i.name, d.pct_chg, d.close, d.pre_close, d.rise, d.lu_days, d.limit_up_num, d.bm_buy_net
    FROM tdx_index i
    JOIN tdx_daily d ON i.ts_code = d.ts_code AND i.trade_date = d.trade_date
    WHERE i.trade_date='20260521' AND i.idx_type='CONCEPT'
    ORDER BY d.pct_chg DESC
    LIMIT 30
""")
print('\nTOP 30 CONCEPT INDICES BY PCT_CHG:')
for row in cur.fetchall():
    print(f'{row[0]}|{row[1]}|PCT:{row[2]:.2f}|CLOSE:{row[3]:.2f}|PRE:{row[4]:.2f}|RISE:{row[5]}|LU:{row[6]}|LIMITUP:{row[7]}|BM:{row[8]:.2f}')

# Our stocks - what concept indices they belong to
targets = ['688551.SH', '301183.SZ', '002222.SZ', '002428.SZ', '002975.SZ']
print('\nSTOCKS_TDX_MEMBER:')
for code in targets:
    cur.execute("""
        SELECT m.con_code, i.name, d.pct_chg, d.rise, d.lu_days
        FROM tdx_member m
        JOIN tdx_index i ON m.con_code = i.ts_code AND m.trade_date = i.trade_date
        JOIN tdx_daily d ON m.con_code = d.ts_code AND m.trade_date = d.trade_date
        WHERE m.ts_code=? AND m.trade_date='20260521'
        ORDER BY d.pct_chg DESC
        LIMIT 10
    """, (code,))
    rows = cur.fetchall()
    print(f'{code}: {len(rows)} concept indices')
    for r in rows:
        print(f'  {r[0]}|{r[1]}|PCT:{r[2]:.2f}|RISE:{r[3]}|LU:{r[4]}')

conn.close()

# Also check what sectors they belong to
conn2 = sqlite3.connect('cache_db/tdx_concept.db')
cur2 = conn2.cursor()
print('\nSTOCKS_TDX_MEMBER_INDUSTRY:')
for code in targets:
    cur2.execute("""
        SELECT m.con_code, i.name, d.pct_chg
        FROM tdx_member m
        JOIN tdx_index i ON m.con_code = i.ts_code AND m.trade_date = i.trade_date
        JOIN tdx_daily d ON m.con_code = d.ts_code AND m.trade_date = d.trade_date
        WHERE m.ts_code=? AND m.trade_date='20260521' AND i.idx_type='INDUSTRY'
        ORDER BY d.pct_chg DESC
        LIMIT 5
    """, (code,))
    rows = cur2.fetchall()
    print(f'{code}: {len(rows)} industry indices')
    for r in rows:
        print(f'  {r[0]}|{r[1]}|PCT:{r[2]:.2f}')
conn2.close()