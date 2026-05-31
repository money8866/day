# -*- coding: utf-8 -*-
"""主题轮动系统 - 全局配置"""
import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")
os.makedirs(CACHE_DIR, exist_ok=True)

DOTENV_PATH = os.path.join(BASE_DIR, "..", "config", ".env")
load_dotenv(os.path.normpath(DOTENV_PATH))

PORTFOLIO_DB = os.path.join(CACHE_DIR, "theme_portfolio.db")
ROTATION_DB = os.path.join(CACHE_DIR, "theme_rotation.db")

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
WECHAT_SCKEY = os.getenv("WECHAT_SCKEY")

# 主题状态机阈值
MIN_THEME_STOCKS = 5
MAINLINE_SCORE = 55
EMERGING_SCORE = 35
DECLINE_DAYS = 3

# 龙头概率模型权重
LEADER_WEIGHTS = {
    "layer": 0.20,
    "trend": 0.15,
    "limit_up": 0.25,
    "turnover": 0.10,
    "purity": 0.10,
    "relative_strength": 0.20,
}

# 盘中启动预警
STARTER_PCT_THRESHOLD = 5.0      # 涨幅触发
STARTER_VOL_RATIO = 2.0          # 量比触发
STARTER_EARLY_DEADLINE = "10:30" # 早启动截止
ALERT_COOLDOWN_SEC = 1800
CHECK_INTERVAL_SEC = 30

# 通达信服务器
TDX_SERVERS = [
    ("180.153.18.170", 7709),
    ("180.153.18.171", 7709),
    ("180.153.39.51", 7709),
    ("119.147.164.60", 7709),
    ("60.191.117.167", 7709),
    ("218.108.47.69", 7709),
    ("218.108.98.244", 7709),
    ("123.125.108.23", 7709),
    ("123.125.108.24", 7709),
    ("59.173.18.69", 7709),
    ("221.231.141.60", 7709),
]

MOMENTUM_W = 0.6
ACC_W = 0.4
