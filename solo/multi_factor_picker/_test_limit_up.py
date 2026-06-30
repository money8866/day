# -*- coding: utf-8 -*-
"""小样本快速验证涨停突破策略"""
import os, sys, time
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

# 测试5只大盘股
codes = ['600519.SH', '601318.SH', '600036.SH', '000001.SZ', '000333.SZ']
print('测试涨停突破检测逻辑...\n')

for code in codes:
    df = pro.stk_factor_pro(ts_code=code, start_date='20240101', end_date='20260620')
    if df is None or len(df) < 60:
        continue
    df = df.sort_values('trade_date').reset_index(drop=True)

    # 找涨停
    limit_ups = df[df['pct_chg'] >= 9.9]
    if len(limit_ups) == 0:
        print(f'{code}: 无涨停')
        continue

    print(f'{code}: 找到{len(limit_ups)}个涨停')
    for _, row in limit_ups.head(3).iterrows():
        print(f'  {row["trade_date"]} 涨幅{row["pct_chg"]:.1f}% 收盘{row["close"]:.2f}')

    time.sleep(0.12)
