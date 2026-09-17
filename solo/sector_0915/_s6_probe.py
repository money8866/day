# -*- coding: utf-8 -*-
"""Step 6 侦察探针（只读）"""
import io
import os
import sqlite3
import pandas as pd

ROOT = r'd:\mystock\solo\sector_0915'
DB = r'D:\mystock\cache_daily\stock_data.db'

print('=' * 70)
print('1) DB schema')
with sqlite3.connect(DB) as con:
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]
    print('tables =', tables)
    for t in tables:
        cur.execute('PRAGMA table_info("%s")' % t)
        cols = [r[1] for r in cur.fetchall()]
        cur.execute('SELECT COUNT(*) FROM "%s"' % t)
        n = cur.fetchone()[0]
        print('  [%s] rows=%s' % (t, n))
        print('     cols =', cols)
    for t in tables:
        try:
            cur.execute('SELECT MIN(trade_date), MAX(trade_date) FROM "%s"' % t)
            print('  [%s] date_range =' % t, cur.fetchone())
        except Exception as e:
            print('  [%s] no trade_date (%s)' % (t, e))

print('=' * 70)
print('2) Step 5 candidate daily')
p = os.path.join(ROOT, 'data', 'sector_stock_candidate_daily.csv')
df = pd.read_csv(p, dtype=str, encoding='utf-8-sig', low_memory=False)
print('shape =', df.shape)
print('cols =', list(df.columns))
print('trade_date range =', df['trade_date'].min(), '->', df['trade_date'].max())
print('n_dates =', df['trade_date'].nunique(), 'n_stocks =', df['ts_code'].nunique())
if 'status' in df.columns:
    print('status dist =', df['status'].value_counts().to_dict())
if 'candidate_type' in df.columns:
    print('candidate_type dist =', df['candidate_type'].value_counts().to_dict())

print('=' * 70)
print('3) Step 5 membership / master')
mb = pd.read_csv(os.path.join(ROOT, 'data', 'sector_membership.csv'), dtype=str, encoding='utf-8-sig')
print('membership shape =', mb.shape, '| cols =', list(mb.columns))
print('is_static dist =', mb['is_static'].value_counts().to_dict() if 'is_static' in mb.columns else 'NA')
print('membership_type dist =', mb['membership_type'].value_counts().to_dict() if 'membership_type' in mb.columns else 'NA')

print('=' * 70)
print('4) output files exist')
for f in ['output/sector_stock_candidate_today.json',
          'output/sector_stock_candidate_pool.json',
          'output/sector_seos_today.json',
          'output/sector_state_today.json']:
    fp = os.path.join(ROOT, f)
    print('  %-52s %s' % (f, os.path.exists(fp)))

print('=' * 70)
print('5) sector_master')
import json
m = json.load(io.open(os.path.join(ROOT, 'sector_master.json'), encoding='utf-8'))
print('master keys =', list(m.keys())[:10])
if 'sectors' in m:
    print('n_sectors =', len(m['sectors']))
    print('sample =', m['sectors'][0] if m['sectors'] else None)
