# -*- coding: utf-8 -*-
"""临时诊断：daily_basic_cache 表结构/覆盖率"""
import sqlite3
c = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print('tables:', tables)
if 'daily_basic_cache' in tables:
    cols = [r[1] for r in c.execute('PRAGMA table_info(daily_basic_cache)')]
    print('daily_basic_cache cols:', cols)
    r = c.execute('SELECT MIN(trade_date), MAX(trade_date), COUNT(*), COUNT(DISTINCT ts_code) FROM daily_basic_cache').fetchone()
    print('daily_basic_cache:', r)
    print('daily_basic_cache 近10交易日行数:')
    for x in c.execute("SELECT trade_date, COUNT(*) FROM daily_basic_cache GROUP BY trade_date ORDER BY trade_date DESC LIMIT 10"):
        print(' ', x)
    r = c.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM daily_basic_cache WHERE ts_code='000001.SZ'").fetchone()
    print('daily_basic_cache 000001.SZ:', r)
if 'fina_indicator_cache' in tables:
    cols = [r[1] for r in c.execute('PRAGMA table_info(fina_indicator_cache)')]
    print('fina_indicator_cache cols:', cols[:30])
    r = c.execute('SELECT COUNT(*) FROM fina_indicator_cache').fetchone()
    print('fina_indicator_cache rows:', r)
