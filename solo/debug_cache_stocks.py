#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查缓存中的股票列表"""
import os, pickle
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")

cache_path = os.path.join(CACHE_DIR, "cache_batch_daily_key_batch_20260529_3111.pkl")

with open(cache_path, 'rb') as f:
    cache_data = pickle.load(f)

df = cache_data['data']

# 检查缓存中有哪些股票
stock_list = df['ts_code'].unique()
print(f"缓存中共有 {len(stock_list)} 只股票")

# 检查特定股票是否在缓存中
check_stocks = ['000001.SZ', '300750.SZ', '600519.SH', '688525.SH', '300476.SZ', '002475.SZ']
for code in check_stocks:
    if code in stock_list:
        print(f"  {code}: 存在")
    else:
        print(f"  {code}: 不存在")

# 显示前20只股票
print(f"\n前20只股票: {list(stock_list[:20])}")

# 检查为什么批量获取会丢失这些股票
# 直接获取000001.SZ的数据
import tushare as ts
from dotenv import load_dotenv
load_dotenv('d:/mystock/config/.env')
pro = ts.pro_api(os.getenv("TUSHARE_TOKEN"))

print("\n直接调用API获取000001.SZ的数据:")
single_df = pro.daily(ts_code='000001.SZ', start_date='20260511', end_date='20260529')
print(single_df)

# 检查批量接口返回的结果
print("\n批量接口获取000001.SZ的数据:")
batch_df = pro.daily(ts_code='000001.SZ,300750.SZ,600519.SH', start_date='20260511', end_date='20260529')
print(batch_df)