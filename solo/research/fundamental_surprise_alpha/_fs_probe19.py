# -*- coding: utf-8 -*-
"""_fs_probe19: 校验 core 重跑后 ic_full.csv 的中性化变体是否已生效"""
import os
import pandas as pd
import numpy as np

OUTD = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')
ic = pd.read_csv(os.path.join(OUTD, 'ic_full.csv'))
print('ic_full 行数', len(ic))
print('列', list(ic.columns))
print('anchors', ic['anchor'].value_counts().to_dict() if 'anchor' in ic.columns else 'NA')
print('variants', ic['variant'].value_counts().to_dict())

z = ic[(ic['anchor'] == 'E1') & (ic['horizon'] == 5) & (ic['period'] == 'ALL')]
print('\n[E1 H5 ALL] variant -> 非 NaN ic_mean 个数 / 总数')
print(z.groupby('variant')['ic_mean'].apply(lambda s: (np.isfinite(s).sum(), len(s))).to_dict())

raw = z[z['variant'] == 'raw'][['feature', 'ic_mean', 'icir', 'n_coh']]
raw = raw[np.isfinite(raw['ic_mean'])]
raw = raw.reindex(raw['ic_mean'].abs().sort_values(ascending=False).index)
print('\n[Raw Top12]')
print(raw.head(12).to_string(index=False))

print('\n[Top6 特征的中性化对比] (raw / mom / val / siz / ind / all)')
for f in raw['feature'].head(6):
    row = {}
    for v in ('raw', 'resid_mom', 'resid_val', 'resid_siz', 'resid_ind', 'resid_all'):
        s = z[(z['variant'] == v) & (z['feature'] == f)]['ic_mean']
        row[v] = round(float(s.iloc[0]), 4) if len(s) and np.isfinite(s.iloc[0]) else None
    print('%-16s %s' % (f, row))

print('\n[spread/disc/baseline/quadrant 行数]')
for n in ('spread.csv', 'disc.csv', 'baseline.csv', 'quadrant.csv',
          'audit_dataset.csv', 'audit_feature.csv'):
    p = os.path.join(OUTD, n)
    d = pd.read_csv(p) if os.path.exists(p) else None
    print('  %-22s %s' % (n, len(d) if d is not None else 'MISSING'))

print('\n[§33 audit_dataset]')
ad = pd.read_csv(os.path.join(OUTD, 'audit_dataset.csv'))
print(ad.T.to_string())

print('\n[§24 baseline E1 H5]')
bl = pd.read_csv(os.path.join(OUTD, 'baseline.csv'))
b5 = bl[(bl['horizon'] == 5) & (bl.get('anchor', 'E1') == 'E1')]
print(b5.to_string(index=False))
