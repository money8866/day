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

# ==================== 评分权重 (V6.2 Future Alpha) ====================
# 核心转变：从 Current Heat -> Future Alpha
# 降低"当前热度"维度权重，提升"未来预测"维度权重
W_TREND = 0.15          # 降权：趋势是滞后指标，反映过去而非未来
W_CAPITAL = 0.18        # 提权：资金/量能是同步指标，放量+资金流入是强信号
W_SENTIMENT = 0.05      # 降权：情绪最快变化，预测力弱
W_PERSISTENCE = 0.02     # 降权：与资金/量能重叠，降权避免重复计分
W_CONTINUATION = 0.12   # 降权：延续概率有一定预测力但非主力
W_LIFECYCLE = 0.05       # 降权：阶段判断有偏差风险
W_LEADER = 0.05          # 降权：龙头是结果不是原因
W_RISK_INV = 0.03        # 降权：风险作为反向因子
W_FORWARD_ALPHA = 0.35   # 新增：Future Alpha预测分（最大权重！）

# ==================== 生命周期加分 ====================
LIFECYCLE_BONUS = {
    "筑底": 10, "启动": 20, "主升": 15,
    "高潮": -10, "调整": -15, "衰退": -15
}

# ==================== 交易信号阈值 ====================
SB_COMPOSITE = 63  # 再降低一点，让更多主题进强买
SB_CAPITAL = 52
SB_TREND = 56
SB_CONTINUATION = 63
SB_STAGES = ["启动", "启动初期", "启动加速", "主升", "主升加速", "主升回调"]
WATCH_COMPOSITE = 64  # 提高关注门槛！
WATCH_CONTINUATION = 82  # 分歧买点：只有延续分 82+ 才算（真正趋势未破）
WATCH_DIV_COMPOSITE = 61  # 分歧买点要求综合分低于此值（真分歧而非一致看好）
HOLD_COMPOSITE = 60  # 大幅提高持有门槛！
HOLD_CONTINUATION = 75

# ==================== Capital 子维度权重 ====================
# V6.1: 提高资金流向权重，降低纯成交额权重
CAP_W_SHARE = 0.10        # MarketShare 市场成交额占比（降权：大跌放量反高分问题）
CAP_W_ACCEL = 0.10        # CapitalAcceleration 资金加速度（降权）
CAP_W_MFLOW = 0.35        # MoneyflowQuality 资金质量（大幅提权：这才是真正的资金流向）
CAP_W_CONC = 0.10         # CapitalConcentration 资金集中度（降权）
CAP_W_PERSIST = 0.10      # CapitalPersistence 资金持续性（降权）
CAP_W_ROTATION = 0.05     # CapitalRotation 资金轮动（降权）
CAP_W_NETFLOW = 0.20      # NetInflowDirection 当日净流入方向（新增）

# ==================== Alpha Gate（资格赛阈值）====================
# V6.3: 两步筛选 - 先过资格赛再排Alpha
# 未通过的主题不进入TOP15，但仍保留在结果中
ALPHA_GATE_TREND = 55       # Trend Quality 门槛
ALPHA_GATE_CAP_PERSIST = 50  # Capital Persistence 百分位门槛
ALPHA_GATE_ROTATION = 50    # Rotation Timing 百分位门槛

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
