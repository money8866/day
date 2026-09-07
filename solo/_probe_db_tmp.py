# -*- coding: utf-8 -*-
import sqlite3
c = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')
cols = [r[1] for r in c.execute('PRAGMA table_info(daily_basic_cache)').fetchall()]
print('daily_basic_cache cols:', cols)
row = c.execute('SELECT MIN(trade_date),MAX(trade_date),COUNT(*),COUNT(DISTINCT ts_code) FROM daily_basic_cache').fetchone()
print('daily_basic_cache min/max/rows/codes =', row)
row = c.execute("SELECT trade_date, COUNT(*) FROM daily_basic_cache GROUP BY trade_date ORDER BY trade_date DESC LIMIT 3").fetchall()
print('last3 by date:', row)
row = c.execute("SELECT trade_date, COUNT(*) FROM daily_basic_cache GROUP BY trade_date ORDER BY trade_date ASC LIMIT 3").fetchall()
print('first3 by date:', row)
# 抽查 000001.SZ
row = c.execute("SELECT * FROM daily_basic_cache WHERE ts_code='000001.SZ' ORDER BY trade_date DESC LIMIT 2").fetchall()
for r in row:
    print('sample 000001.SZ:', r)
