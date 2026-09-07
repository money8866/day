# -*- coding: utf-8 -*-
import sqlite3
DB = r'D:\mystock\cache_daily\stock_data.db'
c = sqlite3.connect(DB, timeout=60.0)
c.execute('PRAGMA query_only=ON')
for t in ('daily_cache', 'daily_basic_cache', 'adj_factor_cache', 'stk_factor_pro'):
    n = c.execute(f'SELECT COUNT(*) FROM {t} WHERE trade_date=?', ('20260904',)).fetchone()[0]
    print(f'{t} 20260904: {n} 行')
n_tr = c.execute("SELECT COUNT(*) FROM daily_basic_cache WHERE trade_date='20260904' AND turnover_rate IS NOT NULL").fetchone()[0]
n_af = c.execute("SELECT COUNT(*) FROM adj_factor_cache WHERE trade_date='20260904' AND adj_factor IS NOT NULL").fetchone()[0]
print(f'daily_basic 20260904 turnover非空: {n_tr}')
print(f'adj_factor 20260904 非空(当前回填未到): {n_af}')
# 极端: 回填后的总天数
print('daily_basic 日数:', c.execute('SELECT COUNT(DISTINCT trade_date) FROM daily_basic_cache').fetchone()[0])
print('adj_factor 日数:', c.execute('SELECT COUNT(DISTINCT trade_date) FROM adj_factor_cache').fetchone()[0])
