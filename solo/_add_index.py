# -*- coding: utf-8 -*-
"""给stock_data.db添加索引加速回测查询"""
import sqlite3, time

db = r'D:\mystock\cache_daily\stock_data.db'
conn = sqlite3.connect(db, timeout=30.0)

cur = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='stk_factor_pro'")
indexes = [r[0] for r in cur.fetchall()]
print(f'现有索引: {indexes}')

if 'idx_stk_date' not in indexes:
    t0 = time.time()
    conn.execute('CREATE INDEX IF NOT EXISTS idx_stk_date ON stk_factor_pro(trade_date)')
    print(f'创建日期索引: {time.time()-t0:.1f}s')

if 'idx_stk_code_date' not in indexes:
    t0 = time.time()
    conn.execute('CREATE INDEX IF NOT EXISTS idx_stk_code_date ON stk_factor_pro(ts_code, trade_date)')
    print(f'创建代码+日期索引: {time.time()-t0:.1f}s')

conn.commit()
conn.close()
print('完成')