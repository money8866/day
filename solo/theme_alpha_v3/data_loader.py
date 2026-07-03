#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V3.0 - 数据加载模块
"""
import os
import sys
import time
import sqlite3
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
sys.path.insert(0, PROJECT_ROOT)

import config
import cache

# 复用原程序的安全方式，防止tushare访问根目录
if not hasattr(os, '_original_expanduser'):
    os._original_expanduser = os.path.expanduser
original_expanduser = os._original_expanduser

def safe_expanduser(path):
    if '~/tk.csv' in path or '\\tk.csv' in path or 'tk.csv' in path:
        return os.path.join(config.CACHE_DIR, 'tk.csv')
    return original_expanduser(path)

os.path.expanduser = safe_expanduser

import tushare as ts

warnings = __import__('warnings')
warnings.filterwarnings("ignore")

ts.set_token(config.TUSHARE_TOKEN)
pro = ts.pro_api()

def get_daily_data(ts_code_list, start_date, end_date):
    """批量获取日线数据 - 复用原程序缓存方式"""
    DAILY_CACHE_DIR = "d:/mystock/cache_daily"
    
    kline_data = []
    missing_codes = []
    
    # 第一遍：从共享缓存读取
    for code in ts_code_list:
        cache_file = os.path.join(DAILY_CACHE_DIR, f"{code}.csv")
        if os.path.exists(cache_file):
            try:
                df_cache = pd.read_csv(cache_file)
                df_cache['trade_date'] = df_cache['trade_date'].astype(str)
                df_cache = df_cache[(df_cache['trade_date'] >= start_date) & 
                                     (df_cache['trade_date'] <= end_date)].copy()
                if len(df_cache) >= 60:
                    kline_data.append(df_cache)
                    continue
            except Exception:
                pass
        missing_codes.append(code)
    
    # 如果有缺失的代码，尝试批量下载（暂时跳过，先用现有数据）
    if missing_codes:
        # print(f"[DataLoader] 缺失 {len(missing_codes)} 只股票数据")
        pass
    
    if kline_data:
        return pd.concat(kline_data, ignore_index=True)
    return pd.DataFrame()

def get_daily_basic(ts_code_list, trade_date):
    """获取基础数据"""
    cache_key = f"daily_basic_{trade_date}"
    cached = cache.cache_get(cache_key, max_age=3600*4)
    if cached is not None:
        return cached
    
    try:
        df = pro.daily_basic(trade_date=trade_date)
        if df is not None and not df.empty:
            cache.cache_set(cache_key, df, max_age=3600*4)
            return df
    except Exception as e:
        print(f"[DataLoader] 获取daily_basic失败: {e}")
    
    return pd.DataFrame()

def get_moneyflow(ts_code_list, start_date, end_date):
    """获取资金流数据"""
    all_dfs = []
    batch_size = 50
    
    for i in range(0, len(ts_code_list), batch_size):
        batch = ts_code_list[i:i+batch_size]
        
        for code in batch:
            cache_key = f"moneyflow_{code}_{start_date}_{end_date}"
            cached = cache.cache_get(cache_key, max_age=3600*24*7)
            if cached is not None:
                all_dfs.append(cached)
                continue
            
            try:
                df = pro.moneyflow(ts_code=code, start_date=start_date, end_date=end_date)
                if df is not None and not df.empty:
                    all_dfs.append(df)
                    cache.cache_set(cache_key, df, max_age=3600*24*7)
                time.sleep(0.12)
            except Exception as e:
                pass
    
    if all_dfs:
        return pd.concat(all_dfs, ignore_index=True)
    return pd.DataFrame()

def get_limit_list(trade_date):
    """获取涨跌停数据"""
    cache_key = f"limit_list_{trade_date}"
    cached = cache.cache_get(cache_key, max_age=3600*12)
    if cached is not None:
        return cached
    
    try:
        df = pro.limit_list_d(trade_date=trade_date)
        if df is not None and not df.empty:
            cache.cache_set(cache_key, df, max_age=3600*12)
            return df
    except Exception as e:
        print(f"[DataLoader] 获取limit_list失败: {e}")
    
    return pd.DataFrame()

def get_top_list(trade_date):
    """获取龙虎榜数据"""
    cache_key = f"top_list_{trade_date}"
    cached = cache.cache_get(cache_key, max_age=3600*12)
    if cached is not None:
        return cached
    
    try:
        df = pro.top_list(trade_date=trade_date)
        if df is not None and not df.empty:
            cache.cache_set(cache_key, df, max_age=3600*12)
            return df
    except Exception as e:
        pass
    
    return pd.DataFrame()

def get_index_daily(ts_code, start_date, end_date):
    """获取指数日线数据"""
    cache_key = f"index_{ts_code}_{start_date}_{end_date}"
    cached = cache.cache_get(cache_key, max_age=3600*6)
    if cached is not None:
        return cached
    
    try:
        df = pro.index_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is not None and not df.empty:
            cache.cache_set(cache_key, df, max_age=3600*6)
            return df
    except Exception as e:
        pass
    
    return pd.DataFrame()

def get_trade_cal(start_date, end_date):
    """获取交易日历"""
    cache_key = f"trade_cal_{start_date}_{end_date}"
    cached = cache.cache_get(cache_key, max_age=3600*24*30)
    if cached is not None:
        return cached
    
    try:
        df = pro.trade_cal(exchange='', start_date=start_date, end_date=end_date)
        if df is not None and not df.empty:
            df = df[df['is_open'] == 1].copy()
            cache.cache_set(cache_key, df, max_age=3600*24*30)
            return df
    except Exception as e:
        pass
    
    return pd.DataFrame()

print("[DataLoader] 数据加载模块加载完成")
