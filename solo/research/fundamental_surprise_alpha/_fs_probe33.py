# -*- coding: utf-8 -*-
"""诊断：total_mv 的年度分布 / mv80 过滤在各年的实际剔除率 / 篮子只数"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fs_engine as E
from fs_common import DATA, Log

log = Log('_fs_probe33.txt')

df = pd.read_parquet(os.path.join(DATA, 'panel.parquet'))
df = E.add_derived(df)
df['total_mv'] = pd.to_numeric(df['total_mv'], errors='coerce')
df['yr'] = df['ann_date'].astype(str).str[:4]

log('=' * 60)
log('1) total_mv 年度分布（万元）')
g = df.groupby('yr')['total_mv']
t = pd.DataFrame({'n': g.size(), 'notna': g.count(),
                  'p10': g.quantile(0.10), 'p50': g.quantile(0.50),
                  'p90': g.quantile(0.90), 'zero': g.apply(lambda s: (s <= 0).sum())})
log(t.to_string(float_format=lambda v: '%.1f' % v))

log('')
log('=' * 60)
log('2) mv>=80亿 保留率（按年）')
df['keep80'] = (df['total_mv'] >= 800000.0) & (~df['ts_code'].str.endswith('.BJ'))
g2 = df.groupby('yr')['keep80']
log(pd.DataFrame({'n': g2.size(), 'keep': g2.sum(),
                  'rate': g2.mean()}).to_string(float_format=lambda v: '%.4f' % v))

log('')
log('=' * 60)
log('3) mv80 子集里，各年 Top 十分位篮子只数（按入场日 k1）')
for f in ('rev_acc',):
    for tag, sub in (('ALL', None), ('MV80', df['keep80'])):
        d = df if sub is None else df[sub]
        d = d[np.isfinite(np.asarray(d[f], float))]
        kk = pd.factorize(np.asarray(d['k1']))[0]
        r = E.rank_pct_1d_group(np.asarray(d[f], float), kk)
        top = d[np.isfinite(r) & (r >= 0.9)].copy()
        top['yr'] = top['ann_date'].astype(str).str[:4]
        q = top.groupby('yr').agg(n_ev=('k1', 'size'), n_day=('k1', 'nunique'))
        q['avg'] = (q['n_ev'] / q['n_day']).round(2)
        log('  %s' % tag)
        log(q.to_string())

log('')
log('=' * 60)
log('4) Top 十分位篮子的市值画像（各年 p50 total_mv，万元）')
for f in ('rev_acc', 'np_qoq'):
    d = df[np.isfinite(np.asarray(df[f], float))]
    kk = pd.factorize(np.asarray(d['k1']))[0]
    r = E.rank_pct_1d_group(np.asarray(d[f], float), kk)
    top = d[np.isfinite(r) & (r >= 0.9)].copy()
    top['yr'] = top['ann_date'].astype(str).str[:4]
    d = d.copy()
    d['all_yr'] = d['ann_date'].astype(str).str[:4]
    a = top.groupby('yr')['total_mv'].median()
    b = d.groupby('all_yr')['total_mv'].median()
    log('  %s  top_p50 vs all_p50' % f)
    log(pd.DataFrame({'top': a, 'all': b, 'ratio': (a / b).round(3)}).to_string())

log.save()
print('DONE')
