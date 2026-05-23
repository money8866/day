# -*- coding: utf-8 -*-
import sqlite3
import pickle

print('=== COMPREHENSIVE CACHE ANALYSIS ===')

# Load the pkl files first
with open('cache_kpl/daily_20260521.pkl', 'rb') as f:
    df_daily = pickle.load(f)
with open('cache_kpl/kpl_concept_20260521.pkl', 'rb') as f:
    df_kpl = pickle.load(f)
with open('cache_kpl/stock_basic.pkl', 'rb') as f:
    df_basic = pickle.load(f)

# Our 5 stocks
targets = ['688551.SH', '301183.SZ', '002222.SZ', '002428.SZ', '002975.SZ']

# Get their names
print('STOCK_NAMES:')
for code in targets:
    row = df_basic[df_basic['ts_code']==code]
    if len(row)>0:
        print(f'{code}: {row["name"].values[0]}')
    else:
        print(f'{code}: N/A')

# Get their data from daily_20260521 (yesterday data)
print('\nYESTERDAY_CLOSE(0521):')
for code in targets:
    row = df_daily[df_daily['ts_code']==code]
    if len(row)>0:
        r = row.iloc[0]
        print(f'{code}|{r["close"]}|{r["pct_chg"]}|{r["vol"]}')

# Top concepts from kpl_concept
print('\nTOP20_KPL_CONCEPT:')
df_sorted = df_kpl.sort_values('hot_num', ascending=False)
for _, r in df_sorted.head(20).iterrows():
    print(f'{r["con_name"]}|{r["hot_num"]}|{str(r["desc"])[:80]}')

# Connect to SQLite databases
print('\n=== DC_CONCEPT.DB ===')
conn = sqlite3.connect('cache_db/dc_concept.db')
cur = conn.cursor()

# Top concepts by strength
cur.execute("SELECT theme_code, name, pct_change, hot, sort, strength FROM dc_concept WHERE trade_date='20260521' ORDER BY strength DESC LIMIT 20")
print('TOP20_BY_STRENGTH:')
for row in cur.fetchall():
    print(f'STR|{row[0]}|{row[1]}|CHG:{row[2]:.2f}|HOT:{row[3]}|SRT:{row[4]}|STR:{row[5]}')

# Top concepts by hot
cur.execute("SELECT theme_code, name, pct_change, hot, sort, strength FROM dc_concept WHERE trade_date='20260521' ORDER BY hot DESC LIMIT 20")
print('\nTOP20_BY_HOT:')
for row in cur.fetchall():
    print(f'HOT|{row[0]}|{row[1]}|CHG:{row[2]:.2f}|HOT:{row[3]}|SRT:{row[4]}|STR:{row[5]}')

# Find our stocks in concept relationships
print('\nSTOCKS_IN_DC_CONCEPT:')
for code in targets:
    cur.execute("SELECT c.theme_code, c.name, c.pct_change, c.hot, c.strength, cc.reason FROM dc_concept_cons cc JOIN dc_concept c ON cc.theme_code=c.theme_code WHERE cc.ts_code=? AND cc.trade_date='20260521' ORDER BY c.strength DESC", (code,))
    rows = cur.fetchall()
    print(f'STOCK:{code} CONCEPTS:{len(rows)}')
    for r in rows:
        print(f'  CON|{r[0]}|{r[1]}|CHG:{r[2]:.2f}|HOT:{r[3]}|STR:{r[4]}|REASON:{str(r[5])[:60]}')

conn.close()

# TDX concept db - concept index daily
print('\n=== TDX_CONCEPT.DB ===')
conn2 = sqlite3.connect('cache_db/tdx_concept.db')
cur2 = conn2.cursor()

# Top concept indices by change
cur2.execute("SELECT ts_code, name, change, hot, idx_type FROM tdx_index WHERE trade_date='20260521' ORDER BY change DESC LIMIT 20")
print('TOP20_CONCEPT_INDICES_BY_CHANGE:')
for row in cur2.fetchall():
    print(f'IDX|{row[0]}|{row[1]}|CHG:{row[2]:.2f}|HOT:{row[3]}|TYPE:{row[4]}')

# Our stocks in tdx concept members
print('\nSTOCKS_IN_TDX_MEMBER:')
for code in targets:
    cur2.execute("SELECT tc.ts_code, tc.con_code, tc.con_name FROM tdx_member tc WHERE tc.ts_code=? AND tc.trade_date='20260521'", (code,))
    rows = cur2.fetchall()
    print(f'STOCK:{code} MEMBERS:{len(rows)}')
    for r in rows:
        print(f'  MEM|{r[1]}|{r[2]}')

conn2.close()

# Historical concept relationships (15万 rows) - find our stocks
print('\n=== TDX HISTORICAL CONCEPTS ===')
conn3 = sqlite3.connect('cache_db/tdx_concept.db')
cur3 = conn3.cursor()
for code in targets:
    cur3.execute("SELECT concept_code, concept_name, ts_code, stock_name, in_date, trade_date FROM dc_concept_cons WHERE ts_code=? ORDER BY trade_date DESC LIMIT 5", (code,))
    rows = cur3.fetchall()
    print(f'HIST_CONCEPTS:{code} TOTAL:{len(rows)}')
    for r in rows:
        print(f'  HC|{r[0]}|{r[1]}|{r[2]}|{r[3]}|IN:{r[4]}|TD:{r[5]}')
conn3.close()