#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查缓存的数据是否是最新的
"""
import sys
import os
import pickle
import pandas as pd

# 加载环境变量
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 加载tushare
from dotenv import load_dotenv
import tushare as ts

DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)
ts.set_token(os.getenv('TUSHARE_TOKEN'))
pro = ts.pro_api()

# 直接从Tushare获取今日数据
print("=== 直接从Tushare获取今日数据（20260529）\n")

# 检查今日A股基本信息
print("=== 检查盛美上海(688082.SH)的今日行情")
df = pro.daily(ts_code='688082.SH', start_date='20260520', end_date='20260529')
print("直接获取到的数据：")
print(df)

if not df.empty:
    print("\n最新日期：", df.iloc[0]['trade_date'], "，最新涨跌幅：", df.iloc[0]['pct_chg'], "%")
else:
    print("\n没有数据")

# 检查缓存的文件
cache_dir = "cache_backbone_tushare"
cache_files = [f for f in os.listdir(cache_dir) if '688082' in f]
print("\n=== 检查缓存的文件:")
for f in cache_files:
    print(f"  {f}")
    try:
        with open(os.path.join(cache_dir, f), 'rb') as fobj:
            data = pickle.load(fobj)
            print("    类型：", type(data))
            if isinstance(data, pd.DataFrame):
                print("    数据大小：", len(data))
                if not data.empty:
                    print("    交易日期范围：", data['trade_date'].min(), "到", data['trade_date'].max())
                    print("    最新日期：", data.iloc[0]['trade_date'], "，涨跌幅：", data.iloc[0]['pct_chg'], "%")
    except Exception as e:
        print(f"    加载缓存失败：{e}")
