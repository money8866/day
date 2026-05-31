#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查特定股票在缓存中的数据"""
import os, pickle
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")

cache_path = os.path.join(CACHE_DIR, "cache_batch_daily_key_batch_20260529_3111.pkl")

with open(cache_path, 'rb') as f:
    cache_data = pickle.load(f)

df = cache_data['data']

# 检查000001.SZ的数据
stock_df = df[df['ts_code'] == '000001.SZ'].sort_values('trade_date', ascending=False)
print("000001.SZ (平安银行) 在缓存中的数据:")
print(stock_df[['ts_code', 'trade_date', 'close', 'amount', 'vol']])

# 检查300750.SZ的数据
stock_df2 = df[df['ts_code'] == '300750.SZ'].sort_values('trade_date', ascending=False)
print("\n300750.SZ (宁德时代) 在缓存中的数据:")
print(stock_df2[['ts_code', 'trade_date', 'close', 'amount', 'vol']])

# 检查为什么成交额很低
print("\n分析: amount单位应该是千元")
print(f"000001.SZ 最新 amount: {stock_df.iloc[0]['amount']} 千元 = {stock_df.iloc[0]['amount']/1000000:.2f} 百万元")
print(f"300750.SZ 最新 amount: {stock_df2.iloc[0]['amount']} 千元 = {stock_df2.iloc[0]['amount']/1000000:.2f} 百万元")