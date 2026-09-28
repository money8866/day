# -*- coding: utf-8 -*-
"""临时冒烟测试：小样本跑通 hep_null 全链路（不产出正式结果）"""
import numpy as np
import pandas as pd
import hep_engine as E
import hep_null as H

d = E.load()
d = E.add_features(d)
dirs = E.freeze_dir(d, E.BASE_COLS, (2018, 2022), horizon=20)
E.apply_dir(d, E.BASE_COLS, dirs)
d = d[d['rep_year'] == 2025].copy()
print('subset', d.shape, 'cohorts', d['sig_k'].nunique())

mkt = H.load_market()
print('mkt', mkt['Oq'].shape)

de = d[d['E'] == 20].reset_index(drop=True)
grp = E.cohorts(de)
print('de', len(de), 'n_grp', len(grp), 'sizes', [len(x) for x in grp][:5])

R1 = H.null_n1(de, 'ex_20', grp)
R2 = H.null_n2(de, 20, mkt)
print('R1', R1.shape, 'finite frac', np.isfinite(R1).mean())
print('R2', R2.shape, 'finite frac', np.isfinite(R2).mean())
r0 = pd.to_numeric(de['ex_20'], errors='coerce').to_numpy()
print('orig finite frac', np.isfinite(r0).mean())
print('R1==orig for all seeds?', bool((np.abs(R1 - r0).sum(axis=1) == 0).all()))
print('R1 seed means', np.round(np.nanmean(R1, axis=1)[:5], 5))

# 一致性：N1 置换应保持每个 cohort 的收益率多重集不变
a = np.sort(np.nan_to_num(R1[0], nan=-9))
b = np.sort(np.nan_to_num(r0, nan=-9))
print('multiset preserved:', bool(np.allclose(a, b)))

rows = H.null_rows(de, 'SIG_RESID', 'ex_20', R1, 'E20', 'T+20', 'N1')
print(pd.DataFrame(rows).to_string())
rows2 = H.null_rows(de, 'SIG_RESID', 'ex_20', R2, 'E20', 'T+20', 'N2')
print(pd.DataFrame(rows2).to_string())

H.ENTRIES = (20,)
H.HORIZONS = (20,)
cdf = H.counterfactual(d)
print(cdf.to_string())
print('SMOKE OK')
