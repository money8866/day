# -*- coding: utf-8 -*-
"""Step 6 侦察探针 2（只读）"""
import io
import os
import json
import sqlite3
import pandas as pd

ROOT = r'd:\mystock\solo\sector_0915'
DB = r'D:\mystock\cache_daily\stock_data.db'

df = pd.read_csv(os.path.join(ROOT, 'data', 'sector_stock_candidate_daily.csv'),
                 dtype=str, encoding='utf-8-sig', low_memory=False)
print('=== candidate_status dist ===')
print(df['candidate_status'].value_counts().to_dict())
print('=== candidate_type x status ===')
print(pd.crosstab(df['candidate_type'], df['candidate_status']).to_string())

print()
print('=== per-date row counts (last 6 dates) ===')
g = df.groupby('trade_date').size()
print(g.tail(6).to_string())
print('per-date count: min=%s max=%s mean=%.1f' % (g.min(), g.max(), g.mean()))

print()
print('=== CANDIDATE only ===')
c = df[df['candidate_status'] == 'CANDIDATE']
print('rows =', len(c), '| stocks =', c['ts_code'].nunique(), '| dates =', c['trade_date'].nunique())
print('per-date CANDIDATE count: min=%s max=%s mean=%.1f' % (
    c.groupby('trade_date').size().min(), c.groupby('trade_date').size().max(),
    c.groupby('trade_date').size().mean()))
print('unique (ts_code, trade_date) =', c[['ts_code', 'trade_date']].drop_duplicates().shape[0])

print()
print('=== WATCH only ===')
w = df[df['candidate_status'] == 'WATCH']
print('rows =', len(w), '| stocks =', w['ts_code'].nunique())
print('candidate_type dist =', w['candidate_type'].value_counts().to_dict())

print()
print('=== date list ===')
ds = sorted(df['trade_date'].unique())
print('n =', len(ds), 'first5 =', ds[:5], 'last5 =', ds[-5:])

print()
print('=== 关键列非空率 ===')
for col in ['primary_theme_id', 'secondary_themes', 'candidate_score', 'theme_opportunity_score',
            'calibrated_opportunity_base', 'diffusion_pattern', 'diffusion_type', 'theme_phase',
            'ret_5', 'distance_ma20', 'drawdown_20', 'volume_ratio_5', 'turnover_5', 'high_20',
            'data_quality', 'listed_days', 'industry']:
    if col in df.columns:
        print('  %-32s nonnull=%.4f' % (col, df[col].notna().mean()))
    else:
        print('  %-32s MISSING' % col)

print()
print('=== membership_type / theme_role 枚举 ===')
print('membership_type =', df['membership_type'].value_counts().to_dict())
print('theme_role =', df['theme_role'].value_counts().to_dict())

print()
print('=== stock_cache.load_stock_basic ===')
import sys
sys.path.insert(0, r'd:\mystock\solo')
try:
    import stock_cache as sc
    sb = sc.load_stock_basic()
    print('stock_basic shape =', None if sb is None else sb.shape)
    if sb is not None:
        print('cols =', list(sb.columns))
        print(sb.head(3).to_string())
except Exception as e:
    print('load_stock_basic FAILED:', e)

print()
print('=== daily_cache 覆盖：候选股票数 ===')
codes = tuple(df['ts_code'].unique()[:5])
with sqlite3.connect(DB) as con:
    q = "SELECT COUNT(DISTINCT ts_code) FROM daily_cache WHERE ts_code IN (%s)" % ','.join(['?'] * len(codes))
    print('sample in daily_cache =', con.execute(q, codes).fetchone())
    print('daily_cache total codes =', con.execute('SELECT COUNT(DISTINCT ts_code) FROM daily_cache').fetchone())
    print('daily_basic total codes =', con.execute('SELECT COUNT(DISTINCT ts_code) FROM daily_basic_cache').fetchone())

print()
print('=== 最后一个交易日样本（CANDIDATE） ===')
last = c[c['trade_date'] == c['trade_date'].max()]
print('n =', len(last))
print(last[['ts_code', 'name', 'primary_theme_id', 'candidate_type', 'candidate_score',
            'calibrated_opportunity_base', 'diffusion_pattern', 'ret_5', 'distance_ma20',
            'drawdown_20', 'volume_ratio_5', 'data_quality']].head(8).to_string())
