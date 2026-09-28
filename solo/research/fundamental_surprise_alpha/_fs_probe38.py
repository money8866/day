# -*- coding: utf-8 -*-
"""probe38：解释 S2(cohort 相对同日全事件) 与 S3(NAV 相对沪深300) 的读数冲突

现象
  rev_acc 在 VALID 财报月：cohort 相对超额 +0.893%（正，99 cohort）
                         NAV 相对超额 -2.20%/年（负）
  两把尺子的基准不同：
     cohort 尺 = 同日「全事件篮子」
     NAV   尺 = 沪深300
  若 VALID 期「财报事件股整体」本身跑输沪深300，则两读数必然反向。

本脚本只做一件事：把「全事件篮子 vs 沪深300」的同期超额量出来（只读，不改任何口径）。
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fs_engine as E
from fs_common import DATA, OUTD, Log
from _fs_trade_gate2 import (load_px, build_day_baskets, simulate_nav,
                             COST, MV_MIN, HOLD)

log = Log('_fs_probe38.txt')

td, cmap, C, O, lvl = load_px()
df = pd.read_parquet(os.path.join(DATA, 'panel.parquet'))
df = E.add_derived(df)
df['total_mv'] = pd.to_numeric(df['total_mv'], errors='coerce')
df = df[df['k1'] + HOLD <= (len(td) - 1)].reset_index(drop=True)
df['ann_date'] = df['ann_date'].astype(str)
df['is_fy'] = df['ann_date'].str[4:6].isin({'01', '04', '07', '08', '10'})
mv80 = (df['total_mv'] >= MV_MIN) & (~df['ts_code'].str.endswith('.BJ'))

per = {'TRAIN': ('20180101', '20231231'),
       'VALID': ('20240101', '20251231'),
       'OOS': ('20260101', '20260930')}

log('=' * 72)
log('A) 标的池尺子（全事件篮子，mv80）vs 沪深300')
rm, n, nd = build_day_baskets(df[mv80].copy(), cmap, C, O, len(td), COST)
navu = simulate_nav(rm, len(td))
s = pd.Series(navu, index=td).dropna()
bl = pd.Series(lvl, index=td)
rows = []
for pn, (a, b) in per.items():
    x = s[(s.index >= a) & (s.index <= b)]
    b0 = bl[(bl.index >= a) & (bl.index <= b)]
    if len(x) < 30:
        continue
    r = x.pct_change().dropna()
    br = b0.pct_change().reindex(r.index).fillna(0.0)
    yrs = len(r) / 242.0
    ex = r - br
    rows.append(dict(period=pn,
                     univ_cagr=float((1 + r).prod() ** (1 / yrs) - 1),
                     bench_cagr=float((1 + br).prod() ** (1 / yrs) - 1),
                     univ_excess=float((1 + r).prod() ** (1 / yrs) - 1
                                       - ((1 + br).prod() ** (1 / yrs) - 1)),
                     ir=float(ex.mean() / ex.std(ddof=1) * np.sqrt(242))
                     if ex.std(ddof=1) > 1e-12 else np.nan))
log(pd.DataFrame(rows).to_string(index=False, float_format=lambda v: '%.4f' % v))

log('')
log('B) Cohort 尺子：全事件篮子5日收益 − 沪深300 同期5日收益（同日平均）')
idx = pd.Series(lvl, index=np.arange(len(td)))
ks = df['k1'].values.astype(int)
ok = ks + HOLD <= len(td) - 1
idxret = idx.reindex(ks[ok] + HOLD).values / idx.reindex(ks[ok]).values - 1.0
tmp = pd.DataFrame({'k1': ks[ok], 'r': df['ret_E1_T5'].values[ok],
                    'ir': idxret}).dropna()
g = tmp.groupby('k1')[['r', 'ir']].mean()
g['exc'] = g['r'] - g['ir']
g['ym'] = [td[int(k)][:6] for k in g.index]
rec = []
for pn, (a, b) in per.items():
    z = g[(g['ym'] >= a[:6]) & (g['ym'] <= b[:6])]
    rec.append(dict(period=pn, cohort=len(z),
                    basket=float(z['r'].mean() * 100),
                    index=float(z['ir'].mean() * 100),
                    excess=float(z['exc'].mean() * 100)))
log(pd.DataFrame(rec).to_string(index=False, float_format=lambda v: '%.3f' % v))
log('  （单位：% / 5交易日）')

log('')
log('C) VALID 期逐月：全事件篮子 vs 沪深300（cohort 均值）')
z = g[(g['ym'] >= '202401') & (g['ym'] <= '202512')]
p = z.groupby('ym')[['r', 'ir', 'exc']].mean() * 100
p['n'] = z.groupby('ym').size()
log(p.round(3).to_string())
log.save()
print('DONE')
