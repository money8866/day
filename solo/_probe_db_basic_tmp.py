# -*- coding: utf-8 -*-
"""临时探针: 检查 daily_cache / daily_basic_cache / stk_factor_pro 覆盖状态"""
import sqlite3

DB = r'D:\mystock\cache_daily\stock_data.db'
conn = sqlite3.connect(DB, timeout=10.0)

tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
print('TABLES:', tables)

for t in ('daily_cache', 'daily_basic_cache', 'stk_factor_pro'):
    if t not in tables:
        print(f'[{t}] 不存在')
        continue
    row = conn.execute(f'SELECT MIN(trade_date), MAX(trade_date), COUNT(*), COUNT(DISTINCT trade_date), COUNT(DISTINCT ts_code) FROM {t}').fetchone()
    print(f'[{t}] min={row[0]} max={row[1]} rows={row[2]} days={row[3]} codes={row[4]}')

print('\ndaily_basic_cache 最近5日覆盖:')
for r in conn.execute("SELECT trade_date, COUNT(*) FROM daily_basic_cache GROUP BY trade_date ORDER BY trade_date DESC LIMIT 5"):
    print(' ', r)

print('\ndaily_cache 最近5日覆盖:')
for r in conn.execute("SELECT trade_date, COUNT(*) FROM daily_cache GROUP BY trade_date ORDER BY trade_date DESC LIMIT 5"):
    print(' ', r)

print('\ndaily_basic_cache schema:')
for r in conn.execute("PRAGMA table_info(daily_basic_cache)"):
    print(' ', r)
print('\ndaily_basic_cache indexes:')
for r in conn.execute("PRAGMA index_list(daily_basic_cache)"):
    print(' ', r)
print('\ndaily_cache indexes:')
for r in conn.execute("PRAGMA index_list(daily_cache)"):
    print(' ', r)

conn.close()
