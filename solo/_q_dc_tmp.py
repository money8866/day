# -*- coding: utf-8 -*-
import sqlite3

DB = r'D:\mystock\cache_daily\stock_data.db'
c = sqlite3.connect(DB, timeout=60)
print('daily_cache   :', c.execute('SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM daily_cache').fetchone())
print('daily_basic   :', c.execute('SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM daily_basic_cache').fetchone())
print('adj_factor    :', c.execute('SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM adj_factor_cache').fetchone())
print('stk_factor MAX:', c.execute('SELECT MAX(trade_date) FROM stk_factor_pro').fetchone())
rows = c.execute('SELECT trade_date, COUNT(*) FROM daily_cache GROUP BY trade_date ORDER BY trade_date').fetchall()
low = [(d, n) for d, n in rows if n < 4000]
print(f'daily_cache 交易日数={len(rows)} 行数<4000 日期={len(low)}')
for x in low[:10]:
    print('   ', x)
c.close()
