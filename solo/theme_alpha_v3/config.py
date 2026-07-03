#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V3.0 - 配置文件
"""
import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
CACHE_DIR = os.path.join(BASE_DIR, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# 加载环境变量 - 复用原程序方式
DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "")

# ==================== 数据过滤配置 ====================
MIN_LISTED_DAYS = 120
MIN_DAILY_TURNOVER = 50000000  # 5000万
MIN_COMPOSITE_SCORE = 0

# ==================== 评分权重配置 ====================
WEIGHTS = {
    "trend": 0.25,
    "capital": 0.20,
    "sentiment": 0.15,
    "persistence": 0.15,
    "lifecycle": 0.10,
    "leader": 0.10,
    "risk": 0.05
}

# ==================== 生命周期加分配置 ====================
LIFECYCLE_BONUS = {
    "Birth": 20,
    "Expansion": 15,
    "MainTrend": 10,
    "Climax": -10,
    "Decline": -30
}

# ==================== 交易信号阈值 ====================
SIGNAL_THRESHOLDS = {
    "strong_buy": {
        "composite": 80,
        "capital": 70,
        "trend": 70,
        "stages": ["Birth", "Expansion"]
    },
    "watch": 65,
    "hold": 55
}

# ==================== 回测配置 ====================
BACKTEST_START = "20250101"
BACKTEST_END = "20261231"

# ==================== 文件路径 ====================
THEME_CACHE = os.path.join(CACHE_DIR, "theme_universe.parquet")
DAILY_CACHE = os.path.join(CACHE_DIR, "daily")
os.makedirs(DAILY_CACHE, exist_ok=True)

# ==================== 输出配置 ====================
OUTPUT_JSON = os.path.join(CACHE_DIR, "theme_alpha_result.json")
OUTPUT_CSV = os.path.join(CACHE_DIR, "theme_alpha_result.csv")

print(f"[Config] 加载完成，缓存目录: {CACHE_DIR}")
