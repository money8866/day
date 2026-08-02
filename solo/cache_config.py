"""
统一缓存路径配置层

将项目中分散的缓存目录（stock_cache、data_fetcher、eld、etf_alpha_ranking 等）
收敛到统一的 CACHE_ROOT，消除相对路径依赖和跨模块缓存分裂问题。

使用方式：
    from cache_config import CACHE_ROOT, STK_FACTOR_DB, PARQUET_DIR, ...

环境变量：
    MSTOCK_CACHE  覆盖缓存根目录（默认 D:\\mystock\\cache_daily）
"""
import os

# ── 统一缓存根目录（可通过环境变量覆盖）──
CACHE_ROOT = os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily")
os.makedirs(CACHE_ROOT, exist_ok=True)

# ── SQLite 数据库 ──
STK_FACTOR_DB = os.path.join(CACHE_ROOT, "stock_data.db")          # stock_cache.py 主库
ELD_DB = os.path.join(CACHE_ROOT, "eld_cache.sqlite")              # eld 模块
ETF_RANKING_DB = os.path.join(CACHE_ROOT, "etf_ranking.db")        # etf_alpha_ranking 模块

# ── Parquet 通用缓存目录（data_fetcher 等统一使用）──
PARQUET_DIR = os.path.join(CACHE_ROOT, "parquet")
os.makedirs(PARQUET_DIR, exist_ok=True)

# ── JSON 配置/映射目录 ──
THEME_MAP_DIR = os.path.join(CACHE_ROOT, "theme_map")
os.makedirs(THEME_MAP_DIR, exist_ok=True)

# ── 分级过期策略（小时）──
EXPIRE = {
    "daily": 24 * 7,          # 日线行情：7 天
    "daily_basic": 24 * 7,    # 每日基本面：7 天
    "moneyflow": 24 * 3,      # 资金流向：3 天
    "income": 24 * 90,        # 利润表：90 天
    "balance": 24 * 90,       # 资产负债表：90 天
    "cashflow": 24 * 90,      # 现金流量表：90 天
    "forecast": 24 * 30,      # 业绩预告：30 天
    "express": 24 * 30,       # 业绩快报：30 天
    "report_rc": 24 * 7,      # 卖方研报：7 天
    "stock_basic": 24 * 30,   # 股票基本信息：30 天
    "default": 24,             # 默认：24 小时
    "eld": 6,                  # eld 模块：6 小时
}


def get_expire_hours(api_name: str) -> int:
    """根据接口名获取过期时间（小时）"""
    return EXPIRE.get(api_name, EXPIRE["default"])
