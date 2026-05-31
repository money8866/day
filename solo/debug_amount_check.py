#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查特定股票成交额"""
import os
import pandas as pd
import tushare as ts
from datetime import datetime, timedelta
from dotenv import load_dotenv

DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN)

# 交易日
now = datetime.now()
query_date = now.strftime('%Y%m%d') if now.hour >= 15 else (now - timedelta(days=1)).strftime('%Y%m%d')
cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
cal = cal[cal['is_open'] == 1]
TRADE_DATE = str(cal[cal['cal_date'] <= query_date]['cal_date'].max())
print(f"交易日: {TRADE_DATE}")

# 检查几只AI算力股票的成交额
test_codes = ['002647.SZ', '002885.SZ', '603662.SH', '600406.SH', '002195.SZ']

start_date = (pd.to_datetime(TRADE_DATE) - timedelta(days=10)).strftime('%Y%m%d')

print(f"\n获取 {test_codes} 的日线数据...")
for code in test_codes:
    try:
        df = pro.daily(ts_code=code, start_date=start_date, end_date=TRADE_DATE)
        if not df.empty:
            df = df.sort_values('trade_date', ascending=False)
            print(f"\n{code}:")
            print(f"  最近5天成交额(千元): {df['amount'].head(5).tolist()}")
            print(f"  最近5天成交额(亿元): {[f'{x/100000000:.4f}' for x in df['amount'].head(5).tolist()]}")
            print(f"  5日平均成交额(亿元): {df['amount'].head(5).mean()/100000000:.4f}")
        else:
            print(f"{code}: 无数据")
    except Exception as e:
        print(f"{code}: 错误 {e}")

# 检查真正高成交额的AI算力股票
print("\n\n检查真正高成交额的AI算力股票...")
# 获取所有日线数据
market_cap = pro.daily_basic(trade_date=TRADE_DATE, fields='ts_code,total_mv,turnover_rate')
all_codes = market_cap['ts_code'].tolist()

start_date = (pd.to_datetime(TRADE_DATE) - timedelta(days=5)).strftime('%Y%m%d')
daily_dfs = []
for i in range(0, len(all_codes), 500):
    batch = all_codes[i:i+500]
    try:
        df = pro.daily(ts_code=','.join(batch), start_date=start_date, end_date=TRADE_DATE)
        if not df.empty:
            daily_dfs.append(df)
        time.sleep(0.1)
    except:
        pass

if daily_dfs:
    daily_all = pd.concat(daily_dfs, ignore_index=True)
    # 计算每只股票的平均成交额
    amount_by_code = daily_all.groupby('ts_code')['amount'].mean()
    # 排序找出成交额最高的
    top_amount = amount_by_code.sort_values(ascending=False).head(20)
    print("\n成交额最高的20只股票:")
    for code, amt in top_amount.items():
        print(f"  {code}: {amt/100000000:.2f}亿元")

# 检查一些知名AI算力股票
print("\n\n检查知名AI算力股票:")
ai_stocks = ['300750.SZ', '002371.SZ', '688256.SH', '300496.SZ', '603986.SH',
             '300782.SZ', '300799.SZ', '688521.SH', '688339.SH', '002049.SZ']
for code in ai_stocks:
    amt = amount_by_code.get(code, None)
    if amt is not None:
        print(f"  {code}: {amt/100000000:.2f}亿元")