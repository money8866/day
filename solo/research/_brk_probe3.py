# -*- coding: utf-8 -*-
"""突破前 Alpha 研究 · 辅助数据勘探（只读）"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')

for f in ('basic.parquet', 'index.parquet', 'panel.parquet'):
    p = os.path.join(OUT, f)
    print(f, 'exists=', os.path.exists(p), os.path.getsize(p) / 1e6 if os.path.exists(p) else '')

print('\n=== basic.parquet ===')
try:
    b = pd.read_parquet(os.path.join(OUT, 'basic.parquet'))
    print(b.shape, list(b.columns))
    print(b.head(3).to_string())
    if 'industry' in b.columns:
        print('行业数', b['industry'].nunique())
        print(b['industry'].value_counts().head(12).to_string())
    if 'list_date' in b.columns:
        print('list_date 样例', b['list_date'].head(3).tolist())
except Exception as e:
    print('ERR', e)
    sb = pd.read_csv(r"D:\mystock\cache_daily\stock_basic.csv", dtype=str, nrows=3)
    print('csv cols', list(sb.columns))

print('\n=== index.parquet ===')
try:
    ix = pd.read_parquet(os.path.join(OUT, 'index.parquet'))
    print(ix.shape, list(ix.columns))
    print(ix.groupby('ts_code').agg(n=('trade_date', 'size'),
                                    d0=('trade_date', 'min'),
                                    d1=('trade_date', 'max')).to_string())
except Exception as e:
    print('ERR', e)

print('\n=== daily_basic 逐月覆盖（校验 2023 起点）===')
import sqlite3
conn = sqlite3.connect(r"D:\mystock\cache_daily\stock_data.db", timeout=120)
r = pd.read_sql_query("SELECT substr(trade_date,1,6) ym, count(*) n, count(turnover_rate) nt, "
                      "count(total_mv) nm FROM daily_basic_cache GROUP BY ym ORDER BY ym", conn)
conn.close()
print(r.head(8).to_string())
print('...')
print(r.tail(3).to_string())
print('月份数', len(r))
