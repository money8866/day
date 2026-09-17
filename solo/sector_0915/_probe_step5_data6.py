# -*- coding: utf-8 -*-
"""Step5 probe 6: dtypes, pools, membership fields, cache columns (throwaway)."""
import sqlite3, json, sys, os
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')
BASE = os.path.dirname(os.path.abspath(__file__))
DB = r'D:\mystock\cache_daily\stock_data.db'
con = sqlite3.connect(DB)

print('=== 1. seos_daily trade_date dtype & range ===')
seos = pd.read_csv(os.path.join(BASE, 'data', 'sector_seos_daily.csv'), dtype={'trade_date': str})
print('range:', seos['trade_date'].min(), seos['trade_date'].max(), 'rows', len(seos))
act = seos[seos['theme_phase'].isin(['EARLY', 'EMERGING', 'CONFIRMING', 'STRONG'])]
cnt = act.groupby('trade_date').size().sort_values(ascending=False)
print('top 15 active dates inside seos_daily:')
print(cnt.head(15).to_string())
print('rows on 20260703:', (seos['trade_date'] == '20260703').sum())

print('\n=== 2. state_history range & 20260703 phases ===')
st = pd.read_csv(os.path.join(BASE, 'data', 'sector_state_history.csv'), dtype={'trade_date': str})
print('cols:', list(st.columns))
print('range:', st['trade_date'].min(), st['trade_date'].max(), 'rows', len(st))
pc = [c for c in st.columns if 'phase' in c]
if pc:
    d = st[st['trade_date'] == '20260703']
    print('20260703', pc[0], ':', d[pc[0]].value_counts().to_dict())

print('\n=== 3. opportunity pool bucket_counts ===')
with open(os.path.join(BASE, 'output', 'sector_opportunity_pool.json'), encoding='utf-8') as f:
    pool = json.load(f)
print('bucket_counts:', pool.get('bucket_counts'))
print('trade_date:', pool.get('trade_date'))
b = pool.get('buckets', {})
for k, v in b.items():
    if isinstance(v, dict):
        print(k, 'dict keys:', list(v.keys())[:8])
    elif isinstance(v, list):
        print(k, 'list len:', len(v), 'sample keys:', list(v[0].keys())[:8] if v else '-')

print('\n=== 4. membership distinct fields ===')
mem = pd.read_csv(os.path.join(BASE, 'data', 'sector_membership.csv'), dtype={'effective_date': str})
for c in ['membership_type', 'membership_origin', 'mapping_method', 'board_type']:
    print(c, ':', mem[c].value_counts().to_dict())
print('\nmembership_weight by origin:')
print(mem.groupby('membership_origin')['membership_weight'].describe()[['min', '25%', '50%', '75%', 'max']].round(3).to_string())
print('\nsample CORE_COMPANY rows:')
cc = mem[mem['membership_origin'] == 'CORE_COMPANY']
print(cc[['ts_code', 'stock_name', 'sector_id', 'membership_type', 'membership_weight', 'mapping_method', 'source_board', 'reason']].head(10).to_string(index=False))
print('\nsample INDUSTRY_DIRECT rows:')
idr = mem[mem['membership_origin'] == 'INDUSTRY_DIRECT']
print(idr[['ts_code', 'stock_name', 'sector_id', 'membership_type', 'membership_weight', 'mapping_method', 'source_board', 'reason']].head(8).to_string(index=False))
print('\nsample RAW_BOARD CORE/PRIMARY rows:')
rc = mem[(mem['membership_origin'] == 'RAW_BOARD') & (mem['membership_type'].isin(['CORE', 'PRIMARY']))]
print(rc[['ts_code', 'stock_name', 'sector_id', 'membership_type', 'membership_weight', 'source_board', 'reason']].head(8).to_string(index=False))

print('\n=== 5. cache columns & ranges ===')
for t in ['daily_cache', 'daily_basic_cache', 'fina_indicator_cache', 'adj_factor_cache']:
    cols = pd.read_sql(f'PRAGMA table_info({t})', con)
    print(t, ':', cols['name'].tolist())
print('daily_cache range:', pd.read_sql('SELECT MIN(trade_date) mn, MAX(trade_date) mx, COUNT(DISTINCT ts_code) n FROM daily_cache', con).to_dict('records'))

print('\n=== 6. fina_indicator ann_date coverage ===')
fi = pd.read_sql("SELECT COUNT(*) n, SUM(CASE WHEN ann_date IS NOT NULL AND ann_date<>'' THEN 1 ELSE 0 END) n_ann, MIN(end_date) min_end, MAX(end_date) max_end, MAX(ann_date) max_ann FROM fina_indicator_cache", con)
print(fi.to_dict('records'))
print(pd.read_sql("SELECT end_date, COUNT(*) n FROM fina_indicator_cache GROUP BY end_date ORDER BY end_date DESC LIMIT 8", con).to_dict('records'))

print('\n=== 7. daily_basic sample 20260703 ===')
db = pd.read_sql("SELECT * FROM daily_basic_cache WHERE trade_date='20260703' LIMIT 3", con)
print(db.to_string(index=False))

print('\n=== 8. membership cross-theme stock counts ===')
sc = mem.groupby('ts_code')['sector_id'].nunique()
print('stocks in N sectors:', sc.value_counts().sort_index().to_dict())

print('\nPROBE6 DONE')
