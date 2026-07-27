"""
ELD V2 系统常量定义
所有魔法数字集中于此，不允许在业务代码中出现硬编码常量
"""

from enum import Enum, auto
from typing import Final


# ──────────────────────────────────────────────
# 版本
# ──────────────────────────────────────────────
VERSION: Final[str] = "2.0.0"
RELEASE_DATE: Final[str] = "2026-07-23"


# ──────────────────────────────────────────────
# 评分五星映射
# ──────────────────────────────────────────────
class StarRating(Enum):
    FIVE_STAR = "★★★★★"
    FOUR_STAR = "★★★★☆"
    THREE_STAR = "★★★☆☆"
    TWO_STAR = "★★☆☆☆"
    ONE_STAR = "★☆☆☆☆"
    ZERO = "☆☆☆☆☆"


STAR_THRESHOLDS: Final[list[tuple[float, StarRating]]] = [
    (90.0, StarRating.FIVE_STAR),
    (70.0, StarRating.FOUR_STAR),
    (50.0, StarRating.THREE_STAR),
    (30.0, StarRating.TWO_STAR),
    (10.0, StarRating.ONE_STAR),
]


# ──────────────────────────────────────────────
# 评分维度名称
# ──────────────────────────────────────────────
DIM_EVENT_QUALITY: Final[str] = "event_quality"
DIM_EARNINGS: Final[str] = "earnings"
DIM_INSTITUTION: Final[str] = "institution"
DIM_CHIP: Final[str] = "chip"
DIM_TREND: Final[str] = "trend"
DIM_INDUSTRY: Final[str] = "industry"
DIM_FRESHNESS: Final[str] = "freshness"
DIM_EXPECTATION_GAP: Final[str] = "expectation_gap"
DIM_SIMILARITY: Final[str] = "similarity"

ALL_DIMENSIONS: Final[list[str]] = [
    DIM_EVENT_QUALITY,
    DIM_EARNINGS,
    DIM_INSTITUTION,
    DIM_CHIP,
    DIM_TREND,
    DIM_INDUSTRY,
    DIM_FRESHNESS,
    DIM_EXPECTATION_GAP,
    DIM_SIMILARITY,
]


# ──────────────────────────────────────────────
# 市场状态枚举
# ──────────────────────────────────────────────
class MarketRegime(Enum):
    BULL = "bull"
    RECOVERY = "recovery"
    WEAK = "weak"
    BEAR = "bear"
    UNKNOWN = "unknown"


MARKET_MULTIPLIER: Final[dict[MarketRegime, float]] = {
    MarketRegime.BULL: 1.05,
    MarketRegime.RECOVERY: 1.00,
    MarketRegime.WEAK: 0.85,
    MarketRegime.BEAR: 0.65,
    MarketRegime.UNKNOWN: 0.90,
}


# ──────────────────────────────────────────────
# 买点状态枚举
# ──────────────────────────────────────────────
class BuyPointState(Enum):
    ANNOUNCEMENT = "ANNOUNCEMENT"
    BREAKOUT = "BREAKOUT"
    FIRST_PULLBACK = "FIRST_PULLBACK"
    BASE_BUILDING = "BASE_BUILDING"
    SECOND_BREAKOUT = "SECOND_BREAKOUT"
    TREND = "TREND"
    NONE = "NONE"


# ──────────────────────────────────────────────
# 数据源常量
# ──────────────────────────────────────────────
CACHE_DEFAULT_EXPIRE_HOURS: Final[int] = 6
CACHE_DEFAULT_DB: Final[str] = "eld_cache.db"
CACHE_DIR: Final[str] = "cache/eld"
SQLITE_CACHE_FILE: Final[str] = "eld_cache.sqlite"

# CSV 列顺序
CSV_COLUMNS: Final[list[str]] = [
    "ts_code",
    "name",
    "industry",
    "theme",
    "announce_date",
    "forecast_pct",
    "els",
    "final_score",
    "rank",
    DIM_EVENT_QUALITY,
    DIM_INSTITUTION,
    DIM_CHIP,
    DIM_TREND,
    DIM_INDUSTRY,
    DIM_FRESHNESS,
    DIM_EXPECTATION_GAP,
    DIM_SIMILARITY,
    "buy_point",
    "recommendation",
]


# ──────────────────────────────────────────────
# 技术指标参数
# ──────────────────────────────────────────────
MA_PERIODS: Final[list[int]] = [5, 10, 20, 60, 120, 250]
TREND_LOOKBACK_DAYS: Final[int] = 250
MOMENTUM_LOOKBACK_DAYS: Final[int] = 60
VOLUME_SURGE_THRESHOLD: Final[float] = 1.5  # 量比阈值
ATR_PERIOD: Final[int] = 14


# ──────────────────────────────────────────────
# 相似度引擎参数
# ──────────────────────────────────────────────
SIMILARITY_TOP_N: Final[int] = 5
SIMILARITY_FEATURES: Final[list[str]] = [
    "market_cap", "alpha_20d", "turnover_rate", "forecast_pct",
    "roe", "ocf_ratio", "chip_concentration", "institution_flow_20d",
    "industry_code",
]


# ──────────────────────────────────────────────
# 公告新鲜度天数区间
# ──────────────────────────────────────────────
FRESHNESS_SCHEDULE: Final[list[tuple[int, float]]] = [
    (1, 100.0),
    (3, 95.0),
    (5, 85.0),
    (10, 70.0),
    (20, 50.0),
    (30, 30.0),
    (60, 10.0),
]
