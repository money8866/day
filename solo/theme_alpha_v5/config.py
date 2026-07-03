#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V5.0 - 配置文件
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# ==================== 数据过滤配置 ====================
MIN_LISTED_DAYS = 120
MIN_DAILY_AMOUNT = 50000000  # 5000万
MIN_THEME_STOCKS = 10        # 主题最少股票数

# ==================== 评分权重配置 ====================
W_TREND = 0.25
W_CAPITAL = 0.20
W_SENTIMENT = 0.15
W_PERSISTENCE = 0.15
W_LIFECYCLE = 0.10
W_LEADER = 0.10
W_RISK_INV = 0.05

# ==================== 生命周期加分 ====================
LIFECYCLE_BONUS = {
    "Birth": 20,
    "Expansion": 15,
    "MainTrend": 10,
    "Climax": -10,
    "Decline": -30
}

# ==================== 交易信号阈值 ====================
SB_COMPOSITE = 80
SB_CAPITAL = 70
SB_TREND = 70
WATCH_COMPOSITE = 65
HOLD_COMPOSITE = 55

# ==================== 数据缓存配置 ====================
CACHE_DAILY_PATH = "d:/mystock/cache_daily"
THEME_MAP_JSON = os.path.join(CACHE_DAILY_PATH, "theme_stock_map_latest.json")
PARQUET_DIR = os.path.join(CACHE_DIR, "parquet")
os.makedirs(PARQUET_DIR, exist_ok=True)

# ==================== 日期配置 ====================
BACKTEST_START = "20250101"
BACKTEST_END = "20261231"
LOOKBACK_DAYS = 120  # 计算所需回溯天数
