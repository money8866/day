# -*- coding: utf-8 -*-
"""Step5 probe 8: final schema confirmation (throwaway)."""
import sqlite3, sys, os
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')
DB = r'D:\mystock\cache_daily\stock_data.db'
con = sqlite3.connect(DB)

print('=== 1. table columns ===')
for t in ['daily_cache', 'daily_basic_cache', 'fina_indicator_cache', 'adj_factor_cache']:
    cols = pd.read_sql(f'PRAGMA table_info({t})', con)
    print(t, ':', cols['name'].tolist())

print('\n=== 2. fina_indicator fields coverage ===')
fi_cols = [c[1] for c in con.execute('PRAGMA table_info(fina_indicator_cache)').fetchall()]
want = ['roe', 'roe_dt', 'grossprofit_margin', 'netprofit_margin', 'or_yoy', 'netprofit_yoy',
        'ocf_to_or', 'debt_to_assets', 'roa', 'roa_dt', 'ann_date', 'end_date', 'ts_code']
print('present:', [c for c in want if c in fi_cols])
print('missing:', [c for c in want if c not in fi_cols])
r = pd.read_sql("SELECT COUNT(*) n FROM fina_indicator_cache WHERE ts_code='000001.SZ'", con)
print('sample count 000001.SZ:', int(r.n[0]))
s = pd.read_sql("SELECT end_date, ann_date, roe, grossprofit_margin, or_yoy, netprofit_yoy, ocf_to_or, debt_to_assets FROM fina_indicator_cache WHERE ts_code='000001.SZ' ORDER BY end_date DESC LIMIT 3", con)
print(s.to_string(index=False))

print('\n=== 3. daily_basic fields ===')
db_cols = [c[1] for c in con.execute('PRAGMA table_info(daily_basic_cache)').fetchall()]
want2 = ['ts_code', 'trade_date', 'turnover_rate', 'turnover_rate_f', 'volume_ratio', 'pe_ttm', 'pb', 'total_mv', 'circ_mv']
print('present:', [c for c in want2 if c in db_cols], '| missing:', [c for c in want2 if c not in db_cols])
s2 = pd.read_sql("SELECT * FROM daily_basic_cache WHERE ts_code='000001.SZ' AND trade_date='20260703'", con)
print(s2.to_string(index=False))

print('\n=== 4. daily/adj fields ===')
d_cols = [c[1] for c in con.execute('PRAGMA table_info(daily_cache)').fetchall()]
a_cols = [c[1] for c in con.execute('PRAGMA table_info(adj_factor_cache)').fetchall()]
print('daily_cache:', d_cols)
print('adj_factor_cache:', a_cols)
r = pd.read_sql("SELECT MIN(trade_date) mn, MAX(trade_date) mx FROM daily_cache", con)
print('daily range:', r.to_dict('records'))

print('\n=== 5. stock_basic parquet ===')
p = r'D:\mystock\solo\sli\cache\stock_basic.parquet'
sb = pd.read_parquet(p)
print('cols:', sb.columns.tolist())
print('rows:', len(sb))
print(sb.head(2).to_string(index=False))
if 'list_date' in sb.columns:
    print('list_date dtype sample:', sb['list_date'].iloc[0], type(sb['list_date'].iloc[0]).__name__)

print('\n=== 6. theme health/score of seos_daily on 20260703 (sample) ===')
seos = pd.read_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'sector_seos_daily.csv'), dtype={'trade_date': str})
d = seos[(seos['trade_date'] == '20260703') & (seos['theme_phase'].isin(['EARLY', 'EMERGING', 'CONFIRMING', 'STRONG']))]
print(d[['sector_id', 'sector_name', 'theme_phase', 'seos_score', 'theme_health',
         'ew_ret_5', 'ew_ret_20', 'breadth_delta_5', 'core_breadth_delta_5']].head(30).to_string(index=False))

con.close()
print('\nPROBE8 DONE')
