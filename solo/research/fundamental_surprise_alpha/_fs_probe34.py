# -*- coding: utf-8 -*-
"""对账：逐 sleeve 事件的（绝对/超额）平均收益 × 年 sleeve 数 × 1/6 资金，
   与 _fs_trade_gate2 的实际 NAV 年收益对比，定位 mv80 差异来源。"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fs_engine as E
from fs_common import DATA, Log

log = Log('_fs_probe34.txt')
HOLD, COST, SLOT = 5, 0.0030, 1.0 / 6

td = pd.read_parquet(os.path.join(DATA, 'calendar.parquet'))['trade_date'].astype(str).values
NCAL = len(td)
kmap = pd.Series(np.arange(NCAL), index=td)
px = pd.read_parquet(os.path.join(DATA, 'price_panel.parquet'),
                     columns=['ts_code', 'trade_date', 'qfq_open', 'qfq_close'])
px['k'] = px['trade_date'].astype(str).map(kmap)
px = px[px['k'].notna()].copy()
px['k'] = px['k'].astype(np.int32)
px = px[(px['qfq_open'] > 0) & (px['qfq_close'] > 0)]
codes, si = np.unique(px['ts_code'].values, return_inverse=True)
C = np.full((len(codes), NCAL), np.nan, np.float32)
O = np.full((len(codes), NCAL), np.nan, np.float32)
C[si, px['k'].values] = px['qfq_close'].values.astype(np.float32)
O[si, px['k'].values] = px['qfq_open'].values.astype(np.float32)
cmap = pd.Series(np.arange(len(codes)), index=codes)
lv = pd.read_parquet(os.path.join(DATA, 'index_level.parquet')).sort_values('trade_date')
lvl = pd.Series(pd.to_numeric(lv['idx_level'], errors='coerce').values,
                index=lv['trade_date'].astype(str).values).reindex(td).ffill().bfill().values

df = pd.read_parquet(os.path.join(DATA, 'panel.parquet'))
df = E.add_derived(df)
df['total_mv'] = pd.to_numeric(df['total_mv'], errors='coerce')
df = df[df['k1'] + HOLD <= NCAL - 1].reset_index(drop=True)
mv80 = (df['total_mv'] >= 800000.0) & (~df['ts_code'].str.endswith('.BJ'))


def tops(col, sub=None):
    d = df if sub is None else df[sub]
    d = d[np.isfinite(np.asarray(d[col], float))]
    kk = pd.factorize(np.asarray(d['k1']))[0]
    r = E.rank_pct_1d_group(np.asarray(d[col], float), kk)
    return d[np.isfinite(r) & (r >= 0.9)]


def sleeve_stats(ev):
    s = ev['ts_code'].map(cmap)
    keep = s.notna().values
    ev2 = ev[keep].copy()
    si2 = s[keep].values.astype(np.int64)
    k1 = ev2['k1'].values.astype(np.int64)
    P0 = O[si2, k1]
    P1 = C[si2, k1 + HOLD]
    idx0 = lvl[k1]
    idx1 = lvl[k1 + HOLD]
    with np.errstate(invalid='ignore', divide='ignore'):
        raw = P1 / P0 - 1.0 - COST
        exc = raw - (idx1 / idx0 - 1.0)
    t = pd.DataFrame({'k1': k1, 'raw': raw, 'exc': exc,
                      'yr': ev2['ann_date'].astype(str).str[:4].values})
    t = t[np.isfinite(t['raw'])]
    # 篮子（入场日等权）后再按年统计：与 sleeve 记账一致
    b = t.groupby(['k1', 'yr'])[['raw', 'exc']].mean().reset_index()
    g = b.groupby('yr')
    out = pd.DataFrame({
        'n_day': g.size(),
        'mean_raw': g['raw'].mean(), 'mean_exc': g['exc'].mean(),
        'sd_raw': g['raw'].std(ddof=1),
    })
    out['implied_ann'] = out['n_day'] * SLOT * out['mean_raw']
    out['implied_ann_exc'] = out['n_day'] * SLOT * out['mean_exc']
    out['avg_basket'] = (t.groupby('yr').size() / g.size()).round(2)
    return out


for nm, ev in (('TOP_rev_acc', tops('rev_acc')),
               ('TOP_rev_acc_mv80', tops('rev_acc', mv80)),
               ('TOP_np_qoq', tops('np_qoq')),
               ('B1_ALL_EVENTS', df)):
    log('=' * 70)
    log('%s' % nm)
    st = sleeve_stats(ev)
    log(st.to_string(float_format=lambda v: '%.4f' % v))
    log('  合计: n_day=%d  implied_ann 求和=%.4f  mean_raw=%.4f'
        % (st['n_day'].sum(), st['implied_ann'].sum(),
           float(np.average(st['mean_raw'], weights=st['n_day']))))

log.save()
print('DONE')
