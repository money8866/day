#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查 daily_basic 成交额数据"""
import os, pickle
import pandas as pd
import tushare as ts
from datetime import datetime, timedelta
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN)
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")

TRADE_DATE = "20260529"

# 直接调用 API 获取 daily_basic
print("调用 pro.daily_basic 获取当日数据...")
df = pro.daily_basic(trade_date=TRADE_DATE, fields='ts_code,total_mv,turnover_rate,amount')
print(f"获取记录数: {len(df)}")
print(f"\n成交额(amount)统计:")
print(df['amount'].describe())

# 转换单位
df['amount_亿'] = df['amount'] / 100000000
print(f"\n成交额(亿元)统计:")
print(df['amount_亿'].describe())

# 检查>=0.1亿的股票数量
high_amount = df[df['amount_亿'] >= 0.1]
print(f"\n成交额>=0.1亿: {len(high_amount)} 只")

# 检查AI终端相关股票
ai_stocks = ['688525.SH', '300476.SZ', '000063.SZ', '002475.SZ', '601138.SH']
print("\nAI终端相关股票:")
for code in ai_stocks:
    row = df[df['ts_code'] == code]
    if not row.empty:
        amt = row.iloc[0]['amount_亿']
        turnover = row.iloc[0]['turnover_rate']
        print(f"  {code}: 成交额={amt:.4f}亿, 换手率={turnover:.2f}%")
    else:
        print(f"  {code}: 无数据")

# 检查缓存
cache_file = os.path.join(CACHE_DIR, "cache_batch_daily_basic_key_daily_basic_20260529_3111.pkl")
if os.path.exists(cache_file):
    print(f"\n缓存存在: {cache_file}")
    with open(cache_file, 'rb') as f:
        data = pickle.load(f)
    print(f"缓存类型: {type(data)}")
    if isinstance(data, dict) and 'data' in data:
        cached_df = data['data']
        print(f"缓存记录数: {len(cached_df)}")
        print(f"缓存成交额统计:")
        print(cached_df['amount'].describe() if 'amount' in cached_df.columns else "无amount字段")