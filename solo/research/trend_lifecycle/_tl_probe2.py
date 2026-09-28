# -*- coding: utf-8 -*-
"""TL-01 探针 2：指数 / stk_factor / stock_basic / 行业 映射"""
import os
import pandas as pd

CD = r'D:\mystock\cache_daily'
PD = os.path.join(CD, 'parquet')

print('=' * 70, flush=True)
print('[1] index_daily 文件（索引）', flush=True)
fs = [f for f in os.listdir(CD) if f.startswith('index_daily') and f.endswith('.parquet')]
fs += [f for f in os.listdir(PD) if f.startswith('index_daily') and f.endswith('.parquet')]
print('  文件数=%d' % len(fs), flush=True)
print('  样例: %s' % sorted(fs)[:20], flush=True)
codes = sorted(set(f.split('_')[2] + '_' + f.split('_')[3] for f in fs if len(f.split('_')) > 4))
print('  指数代码数=%d' % len(codes), flush=True)
print('  %s' % codes[:60], flush=True)

print('=' * 70, flush=True)
print('[2] stk_factor 样例', flush=True)
sf = [os.path.join(PD, f) for f in os.listdir(PD)
      if f.startswith('stk_factor') and f.endswith('.parquet')]
if not sf:
    sf = [os.path.join(CD, f) for f in os.listdir(CD)
          if f.startswith('stk_factor') and f.endswith('.parquet')]
print('  文件数=%d' % len(sf), flush=True)
if sf:
    d = pd.read_parquet(sf[0])
    print('  shape=%s' % (d.shape,), flush=True)
    print('  cols=%s' % list(d.columns), flush=True)
    print(d.head(2).to_string(), flush=True)

print('=' * 70, flush=True)
print('[3] stock_basic', flush=True)
for nm in ('stock_basic.csv', 'stock_basic_L.parquet'):
    fp = os.path.join(CD, nm)
    if os.path.exists(fp):
        d = pd.read_csv(fp, dtype=str) if nm.endswith('csv') else pd.read_parquet(fp)
        print('  %s shape=%s' % (nm, d.shape), flush=True)
        print('  cols=%s' % list(d.columns), flush=True)
        print(d.head(3).to_string(), flush=True)

print('=' * 70, flush=True)
print('[4] 行业映射', flush=True)
fp = os.path.join(CD, 'industry', 'sw_industry_map.csv')
if os.path.exists(fp):
    d = pd.read_csv(fp, dtype=str)
    print('  sw_industry_map shape=%s cols=%s' % (d.shape, list(d.columns)), flush=True)
    print(d.head(3).to_string(), flush=True)

print('=' * 70, flush=True)
print('[5] 交易日历 trade_cal', flush=True)
tc = [f for f in os.listdir(CD) if f.startswith('trade_cal') and f.endswith('.parquet')]
print('  文件数=%d 例:%s' % (len(tc), sorted(tc)[:3]), flush=True)
if tc:
    d = pd.read_parquet(os.path.join(CD, sorted(tc)[-1]))
    print('  cols=%s shape=%s' % (list(d.columns), d.shape), flush=True)
    print(d.head(2).to_string(), flush=True)
