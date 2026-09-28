# -*- coding: utf-8 -*-
"""_fs_probe20: 诊断 neutralize 行业分支负编码问题"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fs_run_robust import load_panel
import fs_engine as E

df = load_panel()
for c in ('ind_l1', 'ind_l2'):
    s = df[c]
    print('%s dtype=%s nunique=%d nan=%d' % (c, s.dtype, s.nunique(dropna=True),
                                             int(s.isna().sum())))
    a = s.astype(str).values
    g = pd.factorize(a)[0]
    print('   astype(str) 后 -1 个数 = %d ; 样例 = %s' % (int((g < 0).sum()), a[:5]))

# 复现 nframe(ind_l2)
R = E.neutralize(df, ['S1_np'], [], min_n=8, ind_col='ind_l2')
print('\nind_l2 中性化 NaN 占比 %.4f' % R['S1_np'].isna().mean())
