#!/usr/bin/env python
# -*- coding: utf-8 -*-
import tushare as ts
from dotenv import load_dotenv
import os

# 加载环境变量
load_dotenv(r"d:\mystock\config\.env")
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN)

# 获取思泰克的基本信息
print('Getting stock info for 301568...')
basic_info = pro.stock_basic(ts_code='301568.SZ')
print(basic_info.to_string())

print('\nChecking which ETF has思泰克...')
import akshare as ak
ETF_POOL = {
    '机器人': '562500.SH',
    '消费电子': '159732.SZ',
    '电力': '159611.SZ',
    '芯片': '159995.SZ',
    '半导体': '512480.SH',
}

print('\n=== Checking思泰克 in all ETFs ===')
for name, code in ETF_POOL.items():
    try:
        simple_code = code.replace('.SH', '').replace('.SZ', '')
        df = ak.fund_portfolio_hold_em(symbol=simple_code, date='2024')
        target = df[df['股票代码'].astype(str).str.contains('301568', na=False)]
        if len(target) > 0:
            print(f'\n✓ Found in {name} ({code})')
            print(target.to_string())
    except Exception as e:
        print(f'{name}: {e}')
