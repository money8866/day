# -*- coding: utf-8 -*-
"""t8 前置探针：确认 §37/§38/§39/§40 所需列是否存在。"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hep_common import DATA, FS_DATA, PREREG

d = pd.read_parquet(os.path.join(DATA, 'hep_events.parquet'))
print('events shape', d.shape)
cols = list(d.columns)
print('cols:')
print(cols)

for key in ('sig_k', 'E', 'rep_year', 'si', 'buy_k', 'entry_k', 'ann_date'):
    print(key, 'in cols:', key in cols)

# 与 §40 参数邻域/§37 年度相关的候选列
pat = [c for c in cols if ('hist' in c or 'win' in c or 'q' in c.lower()[:2]
                           or 'accel' in c or 'ocf' in c or 'ar_' in c or 'inv_' in c)]
print('candidate cols:', pat)

ip = pd.read_parquet(os.path.join(FS_DATA, 'index_panel.parquet'))
print('index_panel', ip.shape, list(ip.columns)[:12])
print(ip.head(3).to_string())

print('regime_rule:', PREREG['regime_rule'])
print('pct_cuts:', PREREG['pct_cuts'], 'hist_windows:', PREREG['hist_windows'])
print('reaction_bins:', PREREG['reaction_bins'])
print('walkforward:', PREREG['walkforward'])
