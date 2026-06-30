# -*- coding: utf-8 -*-
"""调试60日新高突破检测"""
import os, sys, time, numpy as np
sys.path.insert(0, r'D:\mystock')
if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break

import tushare as ts
import pandas as pd
ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

codes = ['600519.SH', '601318.SH', '000001.SZ']
for code in codes:
    df = pro.stk_factor_pro(ts_code=code, start_date='20240101', end_date='20260620')
    if df is None or len(df) < 100:
        print(code, '数据不足')
        continue
    df = df.sort_values('trade_date').reset_index(drop=True)
    closes = df['close'].values
    n = len(df)
    print(f'\n{code} 共{n}行数据')

    # 统计：多少个60日新高
    count = 0
    for i in range(60, n):
        high_60 = closes[i-60:i].max()
        if closes[i] > high_60:
            count += 1
    print(f'  60日新高次数: {count}')

    # 找第一个符合条件的信号
    for i in range(60, min(n-10, 200)):
        high_60 = closes[i-60:i].max()
        if closes[i] <= high_60:
            continue

        # 向前找一波拉升
        for lookback in range(5, 80):
            end_idx = i - lookback
            if end_idx < 25:
                continue
            window = closes[end_idx-20:end_idx+1]
            if len(window) < 21:
                continue
            low_idx = np.argmin(window)
            high_idx = np.argmax(window)
            if high_idx <= low_idx:
                continue
            surge = (window[high_idx] - window[low_idx]) / window[low_idx]
            if surge < 0.15:  # 放宽到15%
                continue

            wave1_high_idx = end_idx - 20 + high_idx
            wave1_high = closes[wave1_high_idx]
            adjust_days = i - wave1_high_idx

            # 检查是否突破wave1高点（放宽到-5%）
            if closes[i] >= wave1_high * 0.95:
                print(f'  找到信号: {df.iloc[i]["trade_date"]} 调整{adjust_days}天 突破{(closes[i]-wave1_high)/wave1_high*100:.1f}%')
                break
        else:
            continue
        break

    time.sleep(0.12)
