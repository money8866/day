#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查 market_cap_dict 数据"""
import os, pickle
import pandas as pd
import tushare as ts
from dotenv import load_dotenv
from datetime import datetime, timedelta

DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN)

now = datetime.now()
query_date = now.strftime('%Y%m%d') if now.hour >= 15 else (now - timedelta(days=1)).strftime('%Y%m%d')
cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
cal = cal[cal['is_open'] == 1]
TRADE_DATE = str(cal[cal['cal_date'] <= query_date]['cal_date'].max())

# 获取 daily_basic
market_cap_df = pro.daily_basic(trade_date=TRADE_DATE, fields='ts_code,total_mv,turnover_rate')
print(f"market_cap_df 记录数: {len(market_cap_df)}")

market_cap_dict = {row['ts_code']: row for _, row in market_cap_df.iterrows()}

# 检查 AI 终端相关股票
ai_stocks = ['688525.SH', '300476.SZ', '002475.SZ', '000063.SZ', '601138.SH', '002241.SZ', '301536.SZ']
print("\nAI终端相关股票在market_cap_dict中的数据:")
for code in ai_stocks:
    if code in market_cap_dict:
        mv = market_cap_dict[code]['total_mv']
        turnover = market_cap_dict[code].get('turnover_rate', 0)
        print(f"  {code}: 总市值={mv/10000:.2f}亿元, 换手率={turnover:.2f}%")
    else:
        print(f"  {code}: 不存在")