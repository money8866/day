# -*- coding: utf-8 -*-
"""_fs_probe22: 检查 spread/disc 的锚点/变体结构"""
import os
import pandas as pd

OUTD = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')
for n in ('spread.csv', 'disc.csv', 'ic_full.csv', 'baseline.csv', 'quadrant.csv'):
    d = pd.read_csv(os.path.join(OUTD, n))
    print('== %s  %d 行' % (n, len(d)))
    print('   columns:', list(d.columns))
    for c in ('anchor', 'variant', 'horizon', 'period'):
        if c in d.columns:
            print('   %-8s %s' % (c, d[c].value_counts(dropna=False).to_dict()))
    if 'anchor' in d.columns and 'variant' in d.columns:
        z = d[(d['anchor'] == 'E1') & (d['horizon'] == 5)]
        print('   E1&H5 行数 %d, 唯一 feature %d, variant %s' % (
            len(z), z['feature'].nunique(), z['variant'].value_counts().to_dict()))
    print()
