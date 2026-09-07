# -*- coding: utf-8 -*-
"""临时探针：对比 stk_factor_pro vs (daily_cache + daily_basic_cache + adj_factor_cache)"""
import sqlite3

DB = r'D:\mystock\cache_daily\stock_data.db'
con = sqlite3.connect(DB, timeout=30)

def q(sql, p=()):
    try:
        return con.execute(sql, p).fetchall()
    except Exception as e:
        print('ERR', e)
        return []

# stk_factor_pro schema + coverage
print('stk_factor_pro cols:')
for r in q("PRAGMA table_info(stk_factor_pro)"):
    print('  ', r[1], r[2])
row = q('SELECT MIN(trade_date), MAX(trade_date), COUNT(*), COUNT(DISTINCT trade_date), COUNT(DISTINCT ts_code) FROM stk_factor_pro')
print('stk_factor_pro coverage:', row)

for t in ('daily_cache', 'daily_basic_cache', 'adj_factor_cache'):
    if [x[0] for x in q("SELECT name FROM sqlite_master WHERE name=?")]:
        row = q(f'SELECT MIN(trade_date), MAX(trade_date), COUNT(*), COUNT(DISTINCT trade_date), COUNT(DISTINCT ts_code) FROM {t}')
        print(f'{t} coverage:', row)
    else:
        print(f'{t}: 不存在')

# stk_factor_pro vs daily_cache 单股逐值 (sample overlap 20260828)
print('\n--- 000001.SZ @20260828: stk_factor vs daily vs dbasic vs adj ---')
for d in ('20260720', '20260828', '20260904'):
    s = q("SELECT trade_date, close, open, high, low, pre_close, pct_chg, vol, amount, adj_factor, turnover_rate, volume_ratio, total_mv, circ_mv FROM stk_factor_pro WHERE ts_code='000001.SZ' AND trade_date=?", (d,))
    dc = q("SELECT close, open, high, low, pre_close, pct_chg, vol, amount FROM daily_cache WHERE ts_code='000001.SZ' AND trade_date=?", (d,))
    db = q("SELECT turnover_rate, turnover_rate_f, volume_ratio, total_mv, circ_mv FROM daily_basic_cache WHERE ts_code='000001.SZ' AND trade_date=?", (d,))
    af = q("SELECT adj_factor FROM adj_factor_cache WHERE ts_code='000001.SZ' AND trade_date=?", (d,))
    print(d, '| stk_factor:', s)
    print(d, '| daily_cache:', dc)
    print(d, '| daily_basic:', db)
    print(d, '| adj_factor :', af)

# 抽样多股多日统计: 存在即比较 close/vol/amount
print('\n--- 抽样对比 stk_factor_pro vs daily_cache (close/vol/amount/pct_chg 一致性) ---')
srows = q("""
  SELECT s.ts_code, s.trade_date, s.close, s.vol, s.amount, s.pct_chg,
         d.close, d.vol, d.amount, d.pct_chg
  FROM stk_factor_pro s JOIN daily_cache d ON s.ts_code=d.ts_code AND s.trade_date=d.trade_date
  WHERE s.ts_code IN ('000001.SZ','300750.SZ','600519.SH','688981.SH') AND s.trade_date>='20260801'
  ORDER BY s.ts_code, s.trade_date LIMIT 60
""")
n_diff = 0
for r in srows:
    same = (abs(r[2]-r[6])<1e-9) and (abs(r[3]-r[7])<1e-9) and (abs(r[5]-r[9])<1e-9)
    if not same:
        n_diff += 1
        print('DIFF', r[0], r[1], 'stk=', r[2:6], 'daily=', r[6:10])
print('total sampled:', len(srows), 'diff rows:', n_diff)
con.close()
