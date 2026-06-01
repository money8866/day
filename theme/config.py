import os

# Tushare token (用于日线/涨停/资金流)
TS_TOKEN = os.getenv("TUSHARE_TOKEN", "")
if not TS_TOKEN:
    from dotenv import load_dotenv
    _env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", ".env")
    if os.path.exists(_env):
        load_dotenv(_env)
    TS_TOKEN = os.getenv("TUSHARE_TOKEN", "")

# 外部数据库：题材表(themes) + 成份股表(portfolio)
PORTFOLIO_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "solo", "cache_backbone_tushare", "theme_portfolio.db"
)

# 本地缓存数据库（评分/轮动/龙头）
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "theme_rotation.db")

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

TOP_N = 20
MIN_STOCKS = 5

# 情绪分权重（游资视角）
EMOTION_WEIGHTS = {
    "limit_ratio": 0.30,      # 涨停家数占比
    "lb_ratio": 0.20,         # 连板家数占比
    "leader_height": 0.15,    # 龙头高度
    "promote_rate": 0.15,     # 晋级率
    "break_rate": 0.10,       # 炸板修正（负向）
    "cm20_ratio": 0.10        # 20cm数量占比
}

# 趋势分权重（机构视角）
TREND_WEIGHTS = {
    "pct_20d": 0.30,          # 20日涨幅
    "pct_10d": 0.20,          # 10日涨幅
    "strong_ratio": 0.20,     # 强势股比例
    "amount_growth": 0.15,    # 成交额增量
    "ma_structure": 0.15      # 均线结构
}

# 旧版权重（保留兼容）
WEIGHTS = {
    "avg_pct": 0.20,
    "limit_ratio": 0.30,
    "up_ratio": 0.10,
    "amount": 0.10,
    "leader_premium": 0.15,
    "height_score": 0.15
}
