# -*- coding: utf-8 -*-
import sqlite3, pandas as pd

DB = r'D:\mystock\cache_daily\stock_data.db'

def q(sql, params=()):
    with sqlite3.connect(DB, timeout=30) as conn:
        return pd.read_sql_query(sql, conn, params=params)

def cnt(sql, params=()):
    with sqlite3.connect(DB, timeout=30) as conn:
        return conn.execute(sql, params).fetchone()[0]

# 各表整体范围
for t in ('stk_factor_pro', 'daily_cache', 'daily_basic_cache', 'adj_factor_cache'):
    try:
        print(t, cnt(f'SELECT COUNT(*) FROM {t}'), q(f'SELECT MIN(trade_date) d0, MAX(trade_date) d1, COUNT(DISTINCT trade_date) nd, COUNT(DISTINCT ts_code) nc FROM {t}').to_dict('records'))
    except Exception as e:
        print(t, 'ERR', e)

print('\n--- stk_factor_pro 按日完整性（重点区间采样） ---')
rows = q("SELECT trade_date, COUNT(*) n FROM stk_factor_pro WHERE trade_date>='20240601' GROUP BY trade_date ORDER BY trade_date")
import datetime
for _, r in rows.iterrows():
    d = r['trade_date']
    if d.endswith(('01', '15')) or d in ('20240801', '20250101', '20250102', '20260828', '20260803', '20260804'):
        print(d, r['n'])

print('\n--- stk_factor_pro 完整日(>=4000)范围 ---')
print(q("SELECT MIN(trade_date) d0, MAX(trade_date) d1, COUNT(*) nd FROM (SELECT trade_date FROM stk_factor_pro GROUP BY trade_date HAVING COUNT(*)>=4000)").to_dict('records'))

print('\n--- daily_basic_cache 按日覆盖(2024起采样) ---')
rows = q("SELECT trade_date, COUNT(*) n FROM daily_basic_cache WHERE trade_date>='20240601' GROUP BY trade_date ORDER BY trade_date")
print('daily_basic 交易日数:', len(rows), '范围:', rows['trade_date'].min(), rows['trade_date'].max() if len(rows) else '-')
for _, r in rows.iterrows():
    if r['trade_date'] >= '20260701':
        print(' ', r['trade_date'], r['n'])

print('\n--- adj_factor_cache 按日覆盖(采样) ---')
rows2 = q("SELECT trade_date, COUNT(*) n FROM adj_factor_cache WHERE trade_date>='20240601' GROUP BY trade_date ORDER BY trade_date")
print('adj 交易日数:', len(rows2), '范围:', rows2['trade_date'].min(), rows2['trade_date'].max() if len(rows2) else '-')
for _, r in rows2.iterrows():
    if r['trade_date'] >= '20260701':
        print(' ', r['trade_date'], r['n'])

print('\n--- 缺失检测：20240801..20260907 期间 daily_cache 有行但 daily_basic_cache<4000 的日子 ---')
rows3 = q("""
SELECT c.trade_date, COUNT(DISTINCT c.ts_code) daily_n,
       (SELECT COUNT(*) FROM daily_basic_cache b WHERE b.trade_date=c.trade_date) db_n
FROM daily_cache c
WHERE c.trade_date BETWEEN '20240801' AND '20260907'
GROUP BY c.trade_date HAVING daily_n>=4000 AND db_n<4000 ORDER BY c.trade_date
""")
print('缺失日数量:', len(rows3))
if len(rows3):
    print(rows3.to_string())
