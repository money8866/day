# -*- coding: utf-8 -*-
"""快速测试 wave2_daily 扫描器"""
import os, sys, time, datetime, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'D:\mystock')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
import pandas as pd, numpy as np, tushare as ts
ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')
import wave2_daily as wd

# Get stock pool
sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code')
mask = sb['ts_code'].str.startswith(('600', '601', '603', '000', '002', '300'))
stocks = sb[mask]['ts_code'].tolist()[:100]
print(f'Stock pool: {len(stocks)} stocks')

# Test scan on each
signals = []
for code in stocks:
    result = wd.scan_stock(code, lookback=90)
    if result:
        signals.append(result)
        print(f'SIGNAL: {result["ts_code"]} | {result["pattern"]} | {result["combo"]} | '
              f'回{result["pullback"]}% | RSI={result["rsi_now"]} | '
              f'入{result["entry_price"]} 止{result["stop_price"]} 目{result["target_price"]}')
    else:
        print(f'  {code}: none')
    time.sleep(0.12)

print(f'\nTotal signals: {len(signals)} / {len(stocks)}')
