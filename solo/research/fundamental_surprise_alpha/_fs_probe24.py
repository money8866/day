# -*- coding: utf-8 -*-
"""校验 §26 entry_anchor.csv 是否覆盖头部候选（rev_acc / np_qoq / np_acc 等）"""
import os
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUTD = os.path.join(HERE, 'out')

ar = pd.read_csv(os.path.join(OUTD, 'entry_anchor.csv'))
print('entry_anchor 行数', len(ar))
print('anchors', ar['anchor'].value_counts().to_dict())
print('features', ar['feature'].nunique())

head = ['rev_acc', 'np_qoq', 'np_acc', 'dp_acc', 'dp_qoq', 'rev_qoq']
sub = ar[ar['feature'].isin(head)]
print('\n头部候选覆盖:')
for f in head:
    s = sub[sub['feature'] == f]
    print('  %-10s anchors=%s horizons=%s' %
          (f, sorted(s['anchor'].unique().tolist()), sorted(s['horizon'].unique().tolist())))

print('\nrev_acc 全锚点明细:')
print(sub[sub['feature'] == 'rev_acc'].to_string(index=False))
