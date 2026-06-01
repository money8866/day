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

WEIGHTS = {
    "avg_pct": 0.20,
    "limit_ratio": 0.30,
    "up_ratio": 0.10,
    "amount": 0.10,
    "leader_premium": 0.15,
    "height_score": 0.15
}
