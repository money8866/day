# -*- coding: utf-8 -*-
"""临时诊断：检查 stock_data.db 表结构与覆盖率"""
import sqlite3
c = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print('tables:', tables)

def info(t):
    if t not in tables:
        print(t, 'NOT EXISTS')
        return
    try:
        row = c.execute(f'SELECT MIN(trade_date), MAX(trade_date), COUNT(*), COUNT(DISTINCT ts_code) FROM {t}').fetchone()
        print(t, 'range=%s~%s rows=%s stocks=%s' % row)
    except Exception as e:
        print(t, 'err', e)

for t in ['daily_cache', 'stk_factor_pro']:
    info(t)

# 每个交易日 stk_factor_pro 行数（近10天）
print('stk_factor_pro 近10交易日行数:')
for r in c.execute("SELECT trade_date, COUNT(*) FROM stk_factor_pro GROUP BY trade_date ORDER BY trade_date DESC LIMIT 10"):
    print(' ', r)

# daily_cache 行数 > 4000 的日期是否都覆盖 stk_factor_pro
print('daily_cache 近10交易日行数:')
for r in c.execute("SELECT trade_date, COUNT(*) FROM daily_cache GROUP BY trade_date ORDER BY trade_date DESC LIMIT 10"):
    print(' ', r)

# daily_cache 中 000001.SZ 的范围
r = c.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM daily_cache WHERE ts_code='000001.SZ'").fetchone()
print('daily_cache 000001.SZ:', r)

# 检查 stk_factor_pro 中 turnover_rate 非空的起始日期
r = c.execute("SELECT MIN(trade_date) FROM stk_factor_pro WHERE turnover_rate IS NOT NULL AND turnover_rate>0 AND ts_code='000001.SZ'").fetchone()
print('stk_factor_pro 000001.SZ turnover_rate 起始:', r[0])
r = c.execute("SELECT MIN(trade_date) FROM stk_factor_pro WHERE ts_code='000001.SZ'").fetchone()
print('stk_factor_pro 000001.SZ 起始:', r[0])

# stk_factor_pro 的列（抽样前几个字段）
cols = [r[1] for r in c.execute('PRAGMA table_info(stk_factor_pro)')]
print('stk_factor_pro ncols=', len(cols))
print('first cols:', cols[:20])
