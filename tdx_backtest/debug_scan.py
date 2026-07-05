# -*- coding: utf-8 -*-
"""调试扫描"""
import pandas as pd
import numpy as np
from data_loader import load_kline
from indicators import MA, RSI

ts_code = '688106.SH'

df = load_kline(ts_code, start_date='20260601')
if df is None or len(df) < 30:
    print('数据不足')
    exit()

df = df.sort_values('trade_date').reset_index(drop=True)
close = df['close']

df['ma5'] = MA(close, 5).values
df['ma20'] = MA(close, 20).values
df['rsi6'] = RSI(close, 6).values

print('总行数:', len(df))
print('日期范围:', df['trade_date'].iloc[0], '至', df['trade_date'].iloc[-1])
print()

# 扫描
found = False
for i in range(20, len(df)):
    date = df.iloc[i]['trade_date']
    
    if date != '20260703':
        continue
    
    print('找到! 索引=', i, '日期=', date)
    
    close_val = df.iloc[i]['close']
    vol = df.iloc[i]['vol']
    ma5 = df.iloc[i]['ma5']
    ma20 = df.iloc[i]['ma20']
    rsi6 = df.iloc[i]['rsi6']
    
    avg_vol = np.mean(df['vol'].iloc[max(0, i-20):i])
    vol_ratio = vol / avg_vol if avg_vol > 0 else 1
    
    mom_20 = (close_val - df.iloc[i-20]['close']) / df.iloc[i-20]['close'] * 100
    
    print('close:', close_val)
    print('ma5:', ma5, '> ma20:', ma20, '=', ma5 > ma20)
    print('rsi6:', rsi6)
    print('vol_ratio:', vol_ratio)
    print('mom_20:', mom_20)
    
    found = True
    
    # 评分
    ma_diff = (ma5 - ma20) / ma20 * 100 if ma20 > 0 else 0
    
    if ma_diff > 5:
        trend_score = 50 + (ma_diff - 5) * 3
    elif ma_diff > 0:
        trend_score = 30 + ma_diff * 4
    elif ma_diff > -5:
        trend_score = 20 + ma_diff
    else:
        trend_score = 0
    
    if mom_20 > 20:
        momentum_score = 70
    elif mom_20 > 10:
        momentum_score = 50 + (mom_20 - 10) * 2
    elif mom_20 > 0:
        momentum_score = 40 + mom_20 * 2
    else:
        momentum_score = 30 + mom_20
    
    if 45 <= rsi6 <= 65:
        rsi_score = 100
    elif rsi6 < 45:
        rsi_score = 70 + rsi6 * 0.6
    else:
        rsi_score = 100 - (rsi6 - 65) * 2
    
    if 1.2 <= vol_ratio <= 2.5:
        vol_score = 100
    elif vol_ratio < 1.2:
        vol_score = 60 + vol_ratio * 30
    else:
        vol_score = 80 - (vol_ratio - 2.5) * 15
    
    total_score = trend_score * 0.25 + momentum_score * 0.30 + rsi_score * 0.25 + vol_score * 0.20
    
    print()
    print('评分:')
    print('  trend_score:', round(trend_score, 1))
    print('  momentum_score:', round(momentum_score, 1))
    print('  rsi_score:', round(rsi_score, 1))
    print('  vol_score:', round(vol_score, 1))
    print('  total_score:', round(total_score, 1))

if not found:
    print('未找到20260703')
