#!/usr/bin/env python
# -*- coding: utf-8 -*-
import akshare as ak

print('Checking 159611 holdings...')
df = ak.fund_portfolio_hold_em(symbol='159611', date='2024')

print(f'\nTotal rows: {len(df)}')
print(f'Columns: {df.columns.tolist()}')

print('\nLooking for 思泰克 (301568)...')
target = df[df['股票代码'].astype(str).str.contains('301568', na=False)]

if len(target) > 0:
    print('Found!')
    print(target.to_string())
else:
    print('NOT found in 159611!')

print(f'\nUnique stocks: {df["股票名称"].nunique()}')

print('\n=== All stock names in 159611 (top 50): ===')
print(df['股票名称'].unique()[:50])

print('\n=== Checking for思泰克 anywhere ===')
target_name = df[df['股票名称'].str.contains('思泰克', na=False)]
if len(target_name) > 0:
    print('Found by name!')
    print(target_name.to_string())

print('\n=== Checking cache file ===')
import os
import pickle
cache_file = 'cache_etf_theme/hold_159611.pkl'
if os.path.exists(cache_file):
    print(f'Cache found, loading...')
    with open(cache_file, 'rb') as f:
        cached_df = pickle.load(f)
    print(f'Cached len: {len(cached_df)}')
    cached_target = cached_df[cached_df['股票代码'].astype(str).str.contains('301568', na=False)]
    if len(cached_target) > 0:
        print('FOUND in cache!')
        print(cached_target.to_string())
