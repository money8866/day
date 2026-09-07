# -*- coding: utf-8 -*-
"""临时探针2：三表覆盖 + daily_cache 与 stk_factor_pro 重叠对比"""
import sqlite3

DB = r'D:\mystock\cache_daily\stock_data.db'
con = sqlite3.connect(DB, timeout=30)
for t in ('daily_cache', 'daily_basic_cache', 'adj_factor_cache', 'stk_factor_pro'):
    ex = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
    if not ex:
        print(t, '不存在')
        continue
    row = con.execute(f'SELECT MIN(trade_date), MAX(trade_date), COUNT(*), COUNT(DISTINCT trade_date), COUNT(DISTINCT ts_code) FROM {t}').fetchone()
    print(t, '=>', row)
print()
print('daily_cache 近10日:')
for r in con.execute("SELECT trade_date, COUNT(*) FROM daily_cache GROUP BY trade_date ORDER BY trade_date DESC LIMIT 10"):
    print(' ', r)
print('daily_basic_cache 近5日:')
for r in con.execute("SELECT trade_date, COUNT(*) FROM daily_basic_cache GROUP BY trade_date ORDER BY trade_date DESC LIMIT 5"):
    print(' ', r)
print('adj_factor_cache 近5日:')
for r in con.execute("SELECT trade_date, COUNT(*) FROM adj_factor_cache GROUP BY trade_date ORDER BY trade_date DESC LIMIT 5"):
    print(' ', r)
print()
print('daily_cache 是否含 000001.SZ 完整连续区间:', con.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM daily_cache WHERE ts_code='000001.SZ'").fetchone())
print('daily_basic_cache 000001.SZ 行数:', con.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM daily_basic_cache WHERE ts_code='000001.SZ'").fetchone())
print('adj_factor_cache 000001.SZ 行数:', con.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM adj_factor_cache WHERE ts_code='000001.SZ'").fetchone())
con.close()
