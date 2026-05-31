#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查日线数据获取"""
import os, pickle
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")

# 交易日
now = datetime.now()
query_date = now.strftime('%Y%m%d') if now.hour >= 15 else (now - timedelta(days=1)).strftime('%Y%m%d')
cal_check = True
import tushare as ts
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN)
cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
cal = cal[cal['is_open'] == 1]
TRADE_DATE = str(cal[cal['cal_date'] <= query_date]['cal_date'].max())

# 检查缓存
cache_key = f"batch_{TRADE_DATE}_3111"
safe_key = cache_key.replace("/", "_").replace(":", "_")
cache_file = os.path.join(CACHE_DIR, f"cache_batch_daily_key_{safe_key}.pkl")
print(f"查找缓存文件: {cache_file}")
print(f"存在: {os.path.exists(cache_file)}")

# 列出cache目录中的batch相关文件
import glob
batch_files = glob.glob(os.path.join(CACHE_DIR, "*batch*"))
print(f"\n批量相关缓存文件: {batch_files}")

# 直接搜索cache文件
all_cache_files = glob.glob(os.path.join(CACHE_DIR, "*.pkl"))
print(f"\n所有pkl文件数量: {len(all_cache_files)}")
for f in all_cache_files[:10]:
    print(f"  {os.path.basename(f)}")

# 尝试读取cache
cache_path = os.path.join(CACHE_DIR, "cache_batch_daily_key_batch_20260529_3111.pkl")
if os.path.exists(cache_path):
    with open(cache_path, 'rb') as f:
        cache_data = pickle.load(f)
    print(f"\n缓存内容类型: {type(cache_data)}")
    if isinstance(cache_data, dict):
        print(f"缓存键: {list(cache_data.keys())}")
        if 'data' in cache_data:
            df = cache_data['data']
            print(f"数据记录数: {len(df)}")
            print(df.head())
    elif isinstance(cache_data, pd.DataFrame):
        print(f"数据记录数: {len(cache_data)}")
        print(cache_data.head())