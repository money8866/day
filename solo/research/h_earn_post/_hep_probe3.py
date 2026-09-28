# -*- coding: utf-8 -*-
"""H-EARN-POST 侦察 3：stk_factor_pro 列与覆盖（潜在全历史价量+估值源）"""
import sqlite3
import pandas as pd

DB = r'D:\mystock\cache_daily\stock_data.db'
c = sqlite3.connect(DB, timeout=300)
cols = [r[1] for r in c.execute('PRAGMA table_info(stk_factor_pro)')]
print('stk_factor_pro 列数 =', len(cols))
for i in range(0, len(cols), 6):
    print('   ', cols[i:i + 6])
print()
print('年度覆盖:')
for y in range(2016, 2027):
    n = c.execute("SELECT COUNT(*) FROM stk_factor_pro WHERE trade_date>='%d0101' AND trade_date<='%d1231'" % (y, y)).fetchone()[0]
    ns = c.execute("SELECT COUNT(DISTINCT ts_code) FROM stk_factor_pro WHERE trade_date>='%d0101' AND trade_date<='%d1231'" % (y, y)).fetchone()[0]
    print('   %d rows=%-9d stocks=%d' % (y, n, ns))
print()
print('样例（2026-08-31 前 3 行，关键列）')
key = [x for x in ('ts_code', 'trade_date', 'close', 'open', 'high', 'low', 'pre_close',
                   'close_qfq', 'open_qfq', 'high_qfq', 'low_qfq', 'pre_close_qfq',
                   'vol', 'amount', 'turnover_rate', 'volume_ratio', 'pe_ttm', 'pb',
                   'ps_ttm', 'dv_ttm', 'total_mv', 'circ_mv', 'total_share',
                   'float_share', 'free_share', 'adj_factor') if x in cols]
d = pd.read_sql_query('SELECT %s FROM stk_factor_pro WHERE trade_date=? LIMIT 3'
                      % ','.join(key), c, params=('20260831',))
pd.set_option('display.width', 250)
print(d.to_string())
print()
print('缺失率（2026-08-31 全样本）:')
d2 = pd.read_sql_query('SELECT %s FROM stk_factor_pro WHERE trade_date=?'
                       % ','.join(key), c, params=('20260831',))
print(d2.isna().mean().round(4).to_string())
c.close()
print('DONE')
