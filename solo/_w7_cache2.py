# -*- coding: utf-8 -*-
import sqlite3
con = sqlite3.connect('file:D:/mystock/cache_daily/stock_data.db?mode=ro', uri=True)
for t in ('index_daily_cache', 'daily_cache', 'daily_basic_cache'):
    print('===', t, '===')
    print('  ', [r[1] for r in con.execute('pragma table_info(%s)' % t)])
    print('   n =', con.execute('select count(*) from %s' % t).fetchone()[0])
print('=== 可用指数 ===')
for r in con.execute("select ts_code, count(*) n, min(trade_date) d0, max(trade_date) d1 from index_daily_cache group by 1 order by 2 desc limit 25"):
    print('  ', r)
con.close()
