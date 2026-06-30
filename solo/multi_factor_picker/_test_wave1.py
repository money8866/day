# -*- coding: utf-8 -*-
"""小样本快速测试一波形态检测"""
import os, sys, time
sys.path.insert(0, r'D:\mystock')
if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break

import tushare as ts
import pandas as pd
import numpy as np
ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

# 测试3只股票
codes = ['688629.SH', '603163.SH', '688041.SH']
for code in codes:
    df = pro.stk_factor_pro(ts_code=code, start_date='20240101', end_date='20260620')
    if df is None or len(df) < 100:
        continue
    df = df.sort_values('trade_date').reset_index(drop=True)
    closes = df['close'].values

    # 找一波拉升
    for i in range(30, min(100, len(df))):
        window = closes[i:i+20]
        low_idx = np.argmin(window[:10])
        high_idx = np.argmax(window[low_idx:]) + low_idx
        if high_idx <= low_idx:
            continue
        gain = (window[high_idx] - window[low_idx]) / window[low_idx]
        if gain >= 0.20:
            wave1_rsi = float(df.iloc[i+high_idx].get('rsi_qfq_6', 50))
            print(f'{code} 一波{gain*100:.1f}% RSI峰值{wave1_rsi:.0f}')
            break

    time.sleep(0.12)
