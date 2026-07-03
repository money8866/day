#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V6.0 - 配置文件
所有参数集中管理，支持调优
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache")
PARQUET_DIR = os.path.join(CACHE_DIR, "parquet")
os.makedirs(PARQUET_DIR, exist_ok=True)

# ==================== 数据路径 ====================
DAILY_CACHE_PATH = "d:/mystock/cache_daily"
THEME_MAP_JSON = os.path.join(DAILY_CACHE_PATH, "theme_stock_map_latest.json")
DC_HOT_CACHE_DIR = "d:/mystock/solo/cache_backbone_tushare/dc_hot"
INDEX_CACHE_DIR = "d:/mystock/cache_daily"
MONEYFLOW_DIR = os.path.join(CACHE_DIR, "moneyflow")
os.makedirs(MONEYFLOW_DIR, exist_ok=True)

# ==================== 过滤参数 ====================
MIN_LISTED_DAYS = 120
MIN_DAILY_AMOUNT = 50000000  # 5000万
MIN_THEME_STOCKS = 5

# ==================== 评分权重 ====================
W_TREND = 0.25
W_CAPITAL = 0.20
W_SENTIMENT = 0.15
W_PERSISTENCE = 0.15
W_LIFECYCLE = 0.10
W_LEADER = 0.10
W_RISK_INV = 0.05

# ==================== 生命周期加分 ====================
LIFECYCLE_BONUS = {
    "启动": 20, "扩张": 15, "主升": 10,
    "高潮": -10, "衰退": -30
}

# ==================== 交易信号阈值 ====================
SB_COMPOSITE = 72
SB_CAPITAL = 70
SB_TREND = 65
SB_STAGES = ["启动", "扩张"]
WATCH_COMPOSITE = 66
HOLD_COMPOSITE = 58

# ==================== 回测配置 ====================
LOOKBACK_DAYS = 120
BACKTEST_START = "20250101"
BACKTEST_END = "20261231"

# ==================== 输出路径 ====================
OUTPUT_JSON = os.path.join(CACHE_DIR, "theme_alpha_v6_result.json")
OUTPUT_CSV = os.path.join(CACHE_DIR, "theme_alpha_v6_result.csv")
