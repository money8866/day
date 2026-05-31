#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查日线数据成交额"""
import os, pickle
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")

TRADE_DATE = "20260529"
cache_path = os.path.join(CACHE_DIR, "cache_batch_daily_key_batch_20260529_3111.pkl")

with open(cache_path, 'rb') as f:
    cache_data = pickle.load(f)

df = cache_data['data']
print(f"总记录数: {len(df)}")
print(f"日期分布:\n{df['trade_date'].value_counts().sort_index()}")

# 检查成交额分布
print(f"\n成交额(amount)统计:")
print(df['amount'].describe())

# 检查每只股票最近5天的平均成交额
stock_avg = df.groupby('ts_code')['amount'].agg(['mean', 'count'])
stock_avg['avg_亿'] = stock_avg['mean'] / 100000000
print(f"\n每只股票平均成交额分布:")
print(stock_avg['avg_亿'].describe())

# 检查成交额>=0.1亿(1000万)的股票数量
high_amount = stock_avg[stock_avg['avg_亿'] >= 0.1]
print(f"\n成交额>=0.1亿: {len(high_amount)} 只")
print(f"成交额<0.1亿: {len(stock_avg) - len(high_amount)} 只")

# 检查AI终端相关的几只股票
ai_terminal_stocks = ['688525.SH', '300476.SZ', '000063.SZ', '002475.SZ', '601138.SH']
print("\nAI终端相关股票成交额:")
for code in ai_terminal_stocks:
    if code in stock_avg.index:
        avg = stock_avg.loc[code, 'avg_亿']
        count = stock_avg.loc[code, 'count']
        print(f"  {code}: 平均成交额={avg:.4f}亿, 数据条数={count}")
    else:
        print(f"  {code}: 无数据")