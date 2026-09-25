# -*- coding: utf-8 -*-
"""Post-FirstBoard Alpha V2.0 · 数据勘探（只读）"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')

p = os.path.join(OUT, 'panel.parquet')
import pyarrow.parquet as pq
sch = pq.read_schema(p)
print('=== panel.parquet schema ===')
print(len(sch.names), 'cols')
print(sch.names)
print('rows', pq.ParquetFile(p).metadata.num_rows)

fb = pd.read_parquet(os.path.join(OUT, 'first_board_events.parquet'))
print('\n=== first_board_events ===')
print(len(fb), 'events')
print(fb.columns.tolist())
print(fb['trade_date'].min(), fb['trade_date'].max())
print(fb['board'].value_counts().to_string())

z = np.load(os.path.join(OUT, 'tr_mats.npz'), allow_pickle=False)
print('\n=== tr_mats keys ===')
ks = sorted(z.files)
for k in ks:
    print(k, z[k].shape, z[k].dtype)
