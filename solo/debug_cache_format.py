#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查缓存中股票代码格式"""
import os, pickle
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")

cache_path = os.path.join(CACHE_DIR, "cache_batch_daily_key_batch_20260529_3111.pkl")

with open(cache_path, 'rb') as f:
    cache_data = pickle.load(f)

df = cache_data['data']

# 检查股票代码格式
stock_list = df['ts_code'].unique()
print(f"缓存中股票数量: {len(stock_list)}")
print(f"股票代码样例: {list(stock_list[:5])}")

# 检查688525.SH是否在缓存中
target_stocks = ['688525.SH', '300476.SZ', '002475.SZ', '000063.SZ', '601138.SH']
for code in target_stocks:
    count = len(df[df['ts_code'] == code])
    print(f"{code}: {'存在' if count > 0 else '不存在'} (记录数: {count})")

# 检查数据日期
print(f"\n缓存日期范围: {df['trade_date'].min()} ~ {df['trade_date'].max()}")
print(f"日期分布:\n{df['trade_date'].value_counts().sort_index()}")