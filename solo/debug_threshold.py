#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查不同成交额门槛下的股票数量"""
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

# 获取所有日线数据
market_cap = pro.daily_basic(trade_date=TRADE_DATE, fields='ts_code,total_mv,turnover_rate')
all_codes = market_cap['ts_code'].tolist()

start_date = (pd.to_datetime(TRADE_DATE) - timedelta(days=5)).strftime('%Y%m%d')

print("获取日线数据...")
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

daily_all = pd.concat(daily_dfs, ignore_index=True)
amount_by_code = daily_all.groupby('ts_code')['amount'].mean() / 100000000  # 转换为亿元
print(f"获取了 {len(amount_by_code)} 只股票的成交额数据")

# 检查不同门槛
thresholds = [0.5, 0.3, 0.2, 0.15, 0.1, 0.08, 0.05, 0.03]
print("\n不同成交额门槛下的股票数量:")
for th in thresholds:
    count = (amount_by_code >= th).sum()
    print(f"  >= {th}亿: {count} 只")

# 检查换手率分布
print("\n换手率分布:")
turnover_dict = {row['ts_code']: row['turnover_rate'] for _, row in market_cap.iterrows()}
turnover_series = pd.Series(list(turnover_dict.values()))
for th in [5, 4, 3, 2, 1]:
    count = (turnover_series >= th).sum()
    print(f"  >= {th}%: {count} 只")

# 组合筛选
print("\n组合筛选（换手率>=3% 且 成交额>=0.1亿）:")
both_pass = 0
for code in amount_by_code.index:
    turnover = turnover_dict.get(code, 0)
    amount = amount_by_code.get(code, 0)
    if turnover >= 3 and amount >= 0.1:
        both_pass += 1
print(f"  通过: {both_pass} 只")

# 如果降低成交额门槛到0.1亿
print("\n使用成交额>=0.1亿，检查能通过流动性的股票:")
ai_codes_with_amount = []
for code in amount_by_code.index:
    amount = amount_by_code.get(code, 0)
    if amount >= 0.1:
        ai_codes_with_amount.append(code)
print(f"  成交额>=0.1亿的股票数: {len(ai_codes_with_amount)}")