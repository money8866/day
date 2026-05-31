#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查日线数据获取问题"""
import os
import pandas as pd
import tushare as ts
from dotenv import load_dotenv
from datetime import datetime, timedelta

DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN)

# 交易日
now = datetime.now()
if now.hour < 15:
    query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
else:
    query_date = now.strftime('%Y%m%d')
cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
cal = cal[cal['is_open'] == 1]
TRADE_DATE = str(cal[cal['cal_date'] <= query_date]['cal_date'].max())

print(f"交易日: {TRADE_DATE}")

# 获取日线数据
start_date = (pd.to_datetime(TRADE_DATE) - pd.Timedelta(days=20)).strftime('%Y%m%d')
print(f"start_date: {start_date}, end_date: {TRADE_DATE}")

# 测试1: 批量获取
print("\n测试1: pro.daily 批量获取")
test_codes = ['000001.SZ', '000002.SZ', '300750.SZ']
try:
    df = pro.daily(ts_code=','.join(test_codes), start_date=start_date, end_date=TRADE_DATE)
    print(f"结果: {len(df)} 条")
    print(df[['ts_code', 'trade_date', 'close', 'amount']].head(10))
except Exception as e:
    print(f"错误: {e}")

# 测试2: 单只获取
print("\n测试2: pro.daily 单只获取")
try:
    df_single = pro.daily(ts_code='300750.SZ', start_date=start_date, end_date=TRADE_DATE)
    print(f"结果: {len(df_single)} 条")
    print(df_single[['ts_code', 'trade_date', 'close', 'amount']].head())
except Exception as e:
    print(f"错误: {e}")

# 测试3: 获取成交额
print("\n测试3: 检查 amount 字段")
try:
    df = pro.daily(ts_code='300750.SZ', start_date=start_date, end_date=TRADE_DATE, fields='ts_code,trade_date,amount')
    print(df)
except Exception as e:
    print(f"错误: {e}")

# 测试4: 批量获取日线数据（和主程序一样）
print("\n测试4: 批量获取日线数据 (500只)")
market_cap = pro.daily_basic(trade_date=TRADE_DATE, fields='ts_code,total_mv,turnover_rate')
print(f"市场数据股票数: {len(market_cap)}")
all_codes = market_cap['ts_code'].tolist()[:500]
try:
    df_batch = pro.daily(ts_code=','.join(all_codes), start_date=start_date, end_date=TRADE_DATE)
    print(f"批量获取结果: {len(df_batch)} 条")
    if not df_batch.empty:
        print(df_batch[['ts_code', 'trade_date', 'close', 'amount']].head(10))
        print(f"\namount 统计:")
        print(df_batch['amount'].describe())
except Exception as e:
    print(f"错误: {e}")