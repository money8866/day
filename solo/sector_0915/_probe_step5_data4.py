# -*- coding: utf-8 -*-
"""Step5 probe 4: exact schemas + validation-date selection (throwaway)."""
import sqlite3, json, sys, os
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')
BASE = os.path.dirname(os.path.abspath(__file__))
DB = r'D:\mystock\cache_daily\stock_data.db'
con = sqlite3.connect(DB)

print('=== 1. tables ===')
tabs = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print(tabs)

print('\n=== 2. membership is_static/origin/confidence ===')
mem = pd.read_csv(os.path.join(BASE, 'data', 'sector_membership.csv'), dtype=str)
print('is_static:', mem['is_static'].value_counts().to_dict())
print('membership_origin:', mem['membership_origin'].value_counts().head(10).to_dict())
conf = mem['membership_confidence'].astype(float)
print('confidence min/max:', conf.min(), conf.max())
print('confidence < 0.55 count:', int((conf < 0.55).sum()))

print('\n=== 3. seos_daily columns ===')
seos_cols = list(pd.read_csv(os.path.join(BASE, 'data', 'sector_seos_daily.csv'), nrows=0).columns)
print(len(seos_cols), seos_cols)

print('\n=== 4. state_history columns ===')
sth_cols = list(pd.read_csv(os.path.join(BASE, 'data', 'sector_state_history.csv'), nrows=0).columns)
print(len(sth_cols), sth_cols)

print('\n=== 5. opportunity pool structure ===')
with open(os.path.join(BASE, 'output', 'sector_opportunity_pool.json'), encoding='utf-8') as f:
    pool = json.load(f)

def skel(o, d=0, k=''):
    pad = '  ' * d
    if isinstance(o, dict):
        print(f'{pad}{k}: dict[{len(o)}]')
        for kk, vv in list(o.items())[:10]:
            skel(vv, d + 1, kk)
    elif isinstance(o, list):
        print(f'{pad}{k}: list[{len(o)}]')
        if o:
            skel(o[0], d + 1, '[0]')
    else:
        print(f'{pad}{k}: {repr(o)[:70]}')
skel(pool)

print('\n=== 6. daily_basic 20260915 coverage ===')
qb = pd.read_sql("SELECT COUNT(*) n, SUM(turnover_rate IS NOT NULL) tr, SUM(volume_ratio IS NOT NULL) vr,"
                 " SUM(pe_ttm IS NOT NULL) pe, SUM(pb IS NOT NULL) pb, SUM(circ_mv IS NOT NULL) cmv,"
                 " SUM(total_mv IS NOT NULL) tmv FROM daily_basic_cache WHERE trade_date='20260915'", con)
print(qb.T)

print('\n=== 7. adj_factor coverage ===')
adj_tabs = [t for t in tabs if 'adj' in t.lower()]
print('adj tables:', adj_tabs)
for t in adj_tabs:
    cols = [c[1] for c in con.execute(f'PRAGMA table_info({t})').fetchall()]
    print(t, cols)
    n = pd.read_sql(f"SELECT COUNT(DISTINCT ts_code) FROM {t} WHERE trade_date='20260915'", con)
    print('distinct ts_code on 20260915:', int(n.iat[0, 0]))

print('\n=== 8. fina_indicator columns ===')
fc = [c[1] for c in con.execute('PRAGMA table_info(fina_indicator_cache)').fetchall()]
print(fc)

print('\n=== 9. active phase dates (validation date selection) ===')
date_col = [c for c in seos_cols if 'date' in c.lower()][0]
sect_col = [c for c in seos_cols if c.endswith('_id')][0]
phase_col = [c for c in seos_cols if 'phase' in c.lower()][0]
print('using cols:', date_col, sect_col, phase_col)
seos_full = pd.read_csv(os.path.join(BASE, 'data', 'sector_seos_daily.csv'),
                        usecols=[date_col, sect_col, phase_col])
ph = seos_full.groupby([date_col, phase_col]).size().unstack(fill_value=0)
for c in ['EARLY', 'EMERGING', 'CONFIRMING', 'STRONG']:
    if c not in ph.columns:
        ph[c] = 0
ph['ACTIVE4'] = ph['EARLY'] + ph['EMERGING'] + ph['CONFIRMING'] + ph['STRONG']
act = ph[ph['ACTIVE4'] > 0].sort_values('ACTIVE4', ascending=False)
print('top 20 dates by EARLY/EMERGING/CONFIRMING/STRONG count:')
print(act.head(20)[['EARLY', 'EMERGING', 'CONFIRMING', 'STRONG', 'ACTIVE4']])

print('\n=== 10. trading calendar in seos window ===')
cal = pd.read_sql("SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date>='20260401' AND trade_date<='20260915' ORDER BY trade_date", con)
print('n_dates:', len(cal), 'first:', cal['trade_date'].iloc[0], 'last:', cal['trade_date'].iloc[-1])

print('\n=== 11. daily load size estimate 20250901+ ===')
n = pd.read_sql("SELECT COUNT(*) n, COUNT(DISTINCT ts_code) ns FROM daily_cache WHERE trade_date>='20250901'", con)
print(n.T)

con.close()
print('\nPROBE4 DONE')
