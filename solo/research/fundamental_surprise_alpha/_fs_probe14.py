# -*- coding: utf-8 -*-
"""列出 panel.parquet 的列名，确认 cohort 键"""
import os
import sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fs_common import DATA

cols = pd.read_parquet(os.path.join(DATA, 'panel.parquet')).columns.tolist()
print('COLS=%d' % len(cols))
key = [c for c in cols if c in ('k', 'k0', 'k1', 'cal_date_k1', 'delay_days',
                                'ann_date', 'end_date', 'period', 'year',
                                'ind_l1', 'ts_code', 'name_at_ev')]
print('KEY:', key)
print('ALL:', ','.join(cols))
