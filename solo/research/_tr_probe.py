# -*- coding: utf-8 -*-
"""首板→第一次分歧→缩量止跌→再启动 研究：数据勘探"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')

p = os.path.join(OUT, 'panel.parquet')
import pyarrow.parquet as pq
sch = pq.ParquetFile(p).schema_arrow
print('=== panel.parquet 列 ===')
print([f.name for f in sch])
print('行数:', pq.ParquetFile(p).metadata.num_rows)

idx = pd.read_parquet(os.path.join(OUT, 'index.parquet'))
print('\n=== index.parquet ===')
print('指数:', sorted(idx['ts_code'].unique().tolist()))
print('日期范围:', idx['trade_date'].min(), idx['trade_date'].max())

fb = pd.read_parquet(os.path.join(OUT, 'first_board_events.parquet'))
print('\n=== first_board_events ===')
print('行数:', len(fb))
print('列:', list(fb.columns))
print('年分布:')
print((fb['trade_date'] // 10000).value_counts().sort_index().to_string())
print('板块:', fb['board'].value_counts().to_dict())
print('行业数:', fb['industry'].nunique())

basic = pd.read_parquet(os.path.join(OUT, 'basic.parquet'))
print('\n=== basic.parquet ===')
print('列:', list(basic.columns))
print('行数:', len(basic))

td = pd.read_csv(os.path.join(OUT, 'trade_dates.csv'))
print('\n交易日:', len(td), td['trade_date'].iloc[0], td['trade_date'].iloc[-1])
