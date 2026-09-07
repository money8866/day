# -*- coding: utf-8 -*-
import sqlite3
c = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db', timeout=30)
for t in ('daily_basic_cache', 'adj_factor_cache'):
    print(t, c.execute('SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM ' + t).fetchone())
rows = c.execute("SELECT key, value FROM cache_meta WHERE key LIKE '%batch%' OR key LIKE '%market_empty%'").fetchall()
print('meta:', dict(rows))
c.close()
