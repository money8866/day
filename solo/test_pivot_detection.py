
import sys
sys.path.insert(0, r'd:\mystock\solo')
import tushare_quant as tq
import pandas as pd
import numpy as np
from etf_resonance.wave3_detector import find_pivots, Pivot

print('=== 检查20260612时的枢轴点 ===')

df = tq.get_hist_data('002371.SZ')
df = df[df['trade_date'] <= '20260612'].copy().sort_values('trade_date').reset_index(drop=True)

pivots = find_pivots(df)
print(f'找到 {len(pivots)} 个枢轴点')
for p in pivots:
    print(f'  {p.date} {p.kind} {p.price:.2f} (idx:{p.idx})')

print('\n=== 检查L2的日期20260608 ===')
for p in pivots:
    if p.date == '20260608':
        print('找到20260608的枢轴点:', p)

