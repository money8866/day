# -*- coding: utf-8 -*-
"""TL-01 产物核对（只读）"""
import os
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')

for f in ('tl01_feature_daily.parquet', 'tl01_state_daily.parquet'):
    p = os.path.join(HERE, f)
    pf = pq.ParquetFile(p)
    print('=' * 78)
    print(f, 'rows=%d rgs=%d size=%.1fMB' % (pf.metadata.num_rows, pf.num_row_groups,
                                             os.path.getsize(p) / 1e6))
    print('cols(%d): %s' % (pf.metadata.num_columns,
                            ','.join(pf.schema_arrow.names)))

pf = pq.ParquetFile(os.path.join(HERE, 'tl01_feature_daily.parquet'))
st = pq.ParquetFile(os.path.join(HERE, 'tl01_state_daily.parquet'))
t = pf.read_row_group(0, columns=['ts_code', 'trade_date', 'k', 'year', 'regime',
                                  'ret_20', 'ts_raw', 'atr_pct_pct', 'fwd_20', 'rel_20',
                                  'mfe_20', 'mae_20', 'buy_ok'])
s = st.read_row_group(0, columns=['state', 'state_prev', 'trend_age', 'state_dur',
                                  'reacc_raw', 'tv'])
print('-' * 78)
print('feature 头 5 行：')
print(pd.DataFrame({c: t.column(c).to_numpy()[:5] for c in t.schema.names}).to_string())
print('-' * 78)
print('state 头 5 行：')
print(pd.DataFrame({c: s.column(c).to_numpy()[:5] for c in s.schema.names}).to_string())

# 全量状态分布（只读 state 列）
tt = st.read(columns=['state'])
fv = pf.read(columns=['year', 'regime'])
sv = tt.column('state').to_numpy()
yvv = fv.column('year').to_numpy()
rgv = fv.column('regime').to_numpy()
print('-' * 78)
print('状态分布（全样本）：')
vc = pd.Series(sv).value_counts().sort_index()
for k2, v2 in vc.items():
    print('  S%-3s %10d  %6.2f%%' % (k2, v2, 100.0 * v2 / len(sv)))
print('-' * 78)
dfy = pd.DataFrame({'year': yvv, 'state': sv})
print('年 × 状态占比(%)：')
pv = dfy.groupby('year')['state'].value_counts(normalize=True).unstack().fillna(0) * 100
print(pv.round(2).to_string())
print('-' * 78)
dfr = pd.DataFrame({'regime': rgv, 'state': sv})
print('Regime × 状态占比(%)：')
pr = dfr.groupby('regime')['state'].value_counts(normalize=True).unstack().fillna(0) * 100
print(pr.round(2).to_string())

# 有效行数 vs 全量
print('-' * 78)
print('feature 行数 %d / state 行数 %d' % (pf.metadata.num_rows, st.metadata.num_rows))
