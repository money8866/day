# -*- coding: utf-8 -*-
"""检查 parameter_grid 参数-取值覆盖 与 audit_dataset 的 asof 掩码字段"""
import os
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUTD = os.path.join(HERE, 'out')

pg = pd.read_csv(os.path.join(OUTD, 'parameter_grid.csv'))
print('parameter_grid 行数', len(pg))
print('\nparam × value 计数:')
print(pg.groupby(['param', 'value']).size().to_string())
print('\n各 param 覆盖的特征数:')
print(pg.groupby('param')['feature'].nunique().to_string())

aud = pd.read_csv(os.path.join(OUTD, 'audit_dataset.csv'))
print('\naudit_dataset 列:', list(aud.columns))
print(aud.to_string(index=False))
