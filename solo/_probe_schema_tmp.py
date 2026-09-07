# -*- coding: utf-8 -*-
import sqlite3
DB = r'D:\mystock\cache_daily\stock_data.db'
conn = sqlite3.connect(DB, timeout=30)
cols = [r[1] for r in conn.execute('PRAGMA table_info(stk_factor_pro)').fetchall()]
print('stk_factor_pro 列数:', len(cols))
for need in ('ts_code', 'trade_date', 'turnover_rate', 'turnover_rate_f', 'volume_ratio',
             'total_mv', 'circ_mv', 'adj_factor'):
    print(' ', need, need in cols)
cols_dc = [r[1] for r in conn.execute('PRAGMA table_info(daily_cache)').fetchall()]
print('daily_cache 列:', cols_dc)
cols_db = [r[1] for r in conn.execute('PRAGMA table_info(daily_basic_cache)').fetchall()]
print('daily_basic_cache 列:', cols_db)
cols_af = [r[1] for r in conn.execute('PRAGMA table_info(adj_factor_cache)').fetchall()]
print('adj_factor_cache 列:', cols_af)
conn.close()
