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
W_TREND = 0.22
W_CAPITAL = 0.18
W_SENTIMENT = 0.13
W_PERSISTENCE = 0.08
W_CONTINUATION = 0.15
W_LIFECYCLE = 0.10
W_LEADER = 0.08
W_RISK_INV = 0.06

# ==================== 生命周期加分 ====================
LIFECYCLE_BONUS = {
    "启动": 20, "扩张": 15, "主升": 10,
    "高潮": -10, "衰退": -30
}

# ==================== 交易信号阈值 ====================
SB_COMPOSITE = 63  # 再降低一点，让更多主题进强买
SB_CAPITAL = 52
SB_TREND = 56
SB_CONTINUATION = 63
SB_STAGES = ["启动", "扩张"]
WATCH_COMPOSITE = 64  # 提高关注门槛！
WATCH_CONTINUATION = 82  # 分歧买点：只有延续分 82+ 才算（真正趋势未破）
WATCH_DIV_COMPOSITE = 61  # 分歧买点要求综合分低于此值（真分歧而非一致看好）
HOLD_COMPOSITE = 60  # 大幅提高持有门槛！
HOLD_CONTINUATION = 75

# ==================== Capital 子维度权重 ====================
CAP_W_SHARE = 0.20        # MarketShare 市场成交额占比
CAP_W_ACCEL = 0.20        # CapitalAcceleration 资金加速度
CAP_W_MFLOW = 0.20        # MoneyflowQuality 资金质量
CAP_W_CONC = 0.15         # CapitalConcentration 资金集中度
CAP_W_PERSIST = 0.15      # CapitalPersistence 资金持续性
CAP_W_ROTATION = 0.10     # CapitalRotation 资金轮动

# Capital 非线性放大参数
CAP_AMPLIFY_POWER = 0.80  # power(s, 0.80): Top10%→92, Top30%→76, Top50%→57, 尾部→28
CAP_AMPLIFY_FLOOR = 5     # 最低分
CAP_AMPLIFY_CEIL = 98     # 最高分

# Moneyflow 获取参数
MONEYFLOW_LOOKBACK_DAYS = 12  # 获取最近N个自然日的 moneyflow

# ==================== 回测配置 ====================
LOOKBACK_DAYS = 120
BACKTEST_START = "20250101"
BACKTEST_END = "20261231"

# ==================== 输出路径 ====================
OUTPUT_JSON = os.path.join(CACHE_DIR, "theme_alpha_v6_result.json")
OUTPUT_CSV = os.path.join(CACHE_DIR, "theme_alpha_v6_result.csv")
