# -*- coding: utf-8 -*-
"""校验 Registry 排序与 §26 展示集合是否含头部候选"""
import os
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUTD = os.path.join(HERE, 'out')

rg = pd.read_csv(os.path.join(OUTD, 'fundamental_surprise_alpha_registry.csv'))
print('registry 行数', len(rg))
print('列:')
print(list(rg.columns))
print('\nstatus 分布:', rg['status'].value_counts().to_dict())
print('\n前 20 行（按文件顺序）:')
cols = [c for c in ['alpha_id', 'feature', 'horizon', 'train_ic', 'validation_ic',
                    'oos_ic', 'icir', 'momentum_residual_alpha', 'status'] if c in rg.columns]
print(rg[cols].head(20).to_string(index=False))

print('\nhead(15) 特征:', rg.head(15)['feature'].tolist())
for f in ['rev_acc', 'np_qoq']:
    s = rg[rg['feature'] == f]
    print('\n%s 在 registry 中的位置: %s' % (f, s.index.tolist()))
