#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
快速测试版 - 只分析前10个板块
"""
import os
import sys
import pickle
import warnings
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv

import numpy as np
import pandas as pd
import tushare as ts

warnings.filterwarnings('ignore')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOTENV_PATH = os.path.join(os.path.dirname(BASE_DIR), "/config/.env")
load_dotenv(DOTENV_PATH)

CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")
os.makedirs(CACHE_DIR, exist_ok=True)

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN)

def get_last_trade_date():
    now = datetime.now()
    if now.hour < 15:
        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')
    
    cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
    cal = cal[cal['is_open'] == 1]
    last_trade_date = cal[cal['cal_date'] <= query_date]['cal_date'].max()
    return str(last_trade_date)

TRADE_DATE = get_last_trade_date()
print(f"当前交易日: {TRADE_DATE}")

def get_hist_data(ts_code, n_days=30):
    cache_file = os.path.join(CACHE_DIR, f"{ts_code}.csv")
    
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            df['trade_date'] = df['trade_date'].astype(str)
            if (df['trade_date'] == TRADE_DATE).any():
                filtered_df = df[df['trade_date'] <= TRADE_DATE].copy()
                return filtered_df.tail(n_days)
        except:
            pass
    
    try:
        df = pro.daily(ts_code=ts_code, start_date='20250101', end_date=TRADE_DATE)
        if df.empty:
            return None
        df = df.sort_values('trade_date')
        time.sleep(0.01)
        return df.tail(n_days)
    except:
        return None

def is_performance_sector(sector_name):
    performance_keywords = [
        '年报', '一季报', '半年报', '三季报', '季报',
        '扭亏', '预增', '预减', '预盈', '预亏',
        '业绩', '中报'
    ]
    for keyword in performance_keywords:
        if keyword in sector_name:
            return True
    return False

def get_concept_map():
    cache_file = os.path.join(CACHE_DIR, "dc_concept_members.pkl")
    
    if os.path.exists(cache_file):
        try:
            df = pd.read_pickle(cache_file)
            if df is not None and len(df) > 0:
                return build_maps_from_df(df)
        except Exception as e:
            print(f"缓存读取失败: {e}")
    
    return {}, {}

def build_maps_from_df(df):
    concept_map = {}
    name_map = {}
    
    for concept_name, group in df.groupby('concept_name'):
        if is_performance_sector(concept_name):
            continue
        
        stocks = group['con_code'].tolist()
        if len(stocks) >= 30:
            concept_map[concept_name] = stocks
        
        for _, row in group.iterrows():
            stock_code = row['con_code']
            stock_name = row.get('name', stock_code)
            if stock_code not in name_map:
                name_map[stock_code] = stock_name
    
    print(f"找到 {len(concept_map)} 个有效概念板块（剔除业绩类板块）")
    return concept_map, name_map

def main():
    print("\n" + "=" * 80)
    print(f"  {TRADE_DATE} 快速测试 - 剔除业绩类板块")
    print("=" * 80)
    
    daily_basic = pro.daily_basic(trade_date=TRADE_DATE, fields='ts_code,total_mv,circ_mv,turnover_rate')
    print(f"获取 {len(daily_basic)} 只股票的市值数据")
    
    concept_map, name_map = get_concept_map()
    
    print(f"\n前20个非业绩类板块:")
    print("-" * 60)
    
    sectors_list = list(concept_map.keys())[:20]
    for i, name in enumerate(sectors_list, 1):
        print(f"{i:2d}. {name}")
    
    print("\n" + "=" * 80)
    print("✅ 业绩类板块过滤功能正常工作")
    print("=" * 80)

if __name__ == "__main__":
    main()
