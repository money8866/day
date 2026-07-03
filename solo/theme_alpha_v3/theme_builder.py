#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V3.0 - 主题池构建模块
"""
import os
import sys
import json
import sqlite3
import warnings
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

warnings.filterwarnings("ignore")

# 加载 Tushare Token
ts.set_token(config.TUSHARE_TOKEN)
pro = ts.pro_api()

def get_trade_date(end_date=None):
    """获取最新交易日"""
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")
    cal = pro.trade_cal(exchange='', start_date='20250101', end_date=end_date)
    cal = cal[cal['is_open'] == 1].sort_values('cal_date', ascending=False)
    return cal.iloc[0]['cal_date']

def build_theme_universe():
    """构建主题池 - 复用原程序方式"""
    # 从缓存文件加载
    cache_path = "d:/mystock/cache_daily/theme_stock_map_latest.json"
    
    if os.path.exists(cache_path):
        with open(cache_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            theme_universe = data.get("themes", {})
            print(f"[ThemeBuilder] 从缓存加载: {len(theme_universe)}个主题")
            return theme_universe
    
    print(f"[ThemeBuilder] 未找到主题数据")
    theme_universe = {}
    return theme_universe

def filter_stocks(ts_code_list, trade_date=None):
    """过滤股票 - 简化版，直接返回所有股票"""
    # 获取所有股票列表
    all_stocks = set()
    theme_json = "d:/mystock/solo/theme.json"
    if os.path.exists(theme_json):
        with open(theme_json, 'r', encoding='utf-8') as f:
            theme_data = json.load(f)
            if "HOT_THEMES" in theme_data:
                hot_themes = theme_data["HOT_THEMES"]
                for theme, info in hot_themes.items():
                    all_stocks.update(list(info.get("STOCKS", {}).keys()))
    
    result = list(all_stocks)
    print(f"[ThemeBuilder] 股票列表: {len(result)}只")
    return result

def get_theme_stocks(theme_name, theme_universe, valid_stocks):
    """获取主题的有效成份股"""
    if theme_name not in theme_universe:
        return []
    
    stocks = theme_universe[theme_name]
    return [s for s in stocks if s in valid_stocks]

if __name__ == "__main__":
    universe = build_theme_universe()
    valid = filter_stocks()
    print(f"主题: {len(universe)}, 有效股票: {len(valid)}")
