"""
ELD V2 系统配置

所有权重、阈值、参数集中管理。
支持环境变量覆盖。
禁止在业务代码中出现魔法数字。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


# ──────────────────────────────────────────────
# 全局配置
# ──────────────────────────────────────────────
@dataclass
class GlobalConfig:
    """全局配置"""
    debug: bool = os.getenv("ELD_DEBUG", "false").lower() == "true"
    log_level: str = os.getenv("ELD_LOG_LEVEL", "INFO")
    log_file: Optional[str] = os.getenv("ELD_LOG_FILE", None)
    data_dir: str = os.getenv("ELD_DATA_DIR", "d:/mystock")
    output_dir: str = os.getenv("ELD_OUTPUT_DIR", "d:/mystock/report_daily")
    cache_dir: str = os.getenv("ELD_CACHE_DIR", "cache/eld")
    max_workers: int = int(os.getenv("ELD_MAX_WORKERS", "8"))
    target_date: Optional[str] = os.getenv("ELD_TARGET_DATE", None)


# ──────────────────────────────────────────────
# Tushare 配置
# ──────────────────────────────────────────────
@dataclass
class TushareConfig:
    """Tushare 数据源配置"""
    token: str = os.getenv("TUSHARE_TOKEN", "") or os.getenv("TUSHARE_TOKEN", "")
    timeout: int = 30
    retry_count: int = 3
    retry_delay: float = 1.0
    rate_limit_ms: int = 120  # 线程安全限流


# ──────────────────────────────────────────────
# 缓存配置
# ──────────────────────────────────────────────
@dataclass
class CacheConfig:
    """缓存配置"""
    sqlite_enabled: bool = True
    csv_enabled: bool = True
    expire_hours: int = 6
    sqlite_db: str = "eld_cache.sqlite"
    sqlite_cache_dir: str = "cache/eld"
    csv_cache_dir: str = "cache/eld/csv"
    incremental_update: bool = True


# ──────────────────────────────────────────────
# 事件过滤配置
# ──────────────────────────────────────────────
@dataclass
class EventFilterConfig:
    """事件过滤配置"""
    # 排除关键字（一次性/非经常性收益）
    exclude_keywords: list[str] = field(default_factory=lambda: [
        "卖资产", "政府补贴", "补贴", "公允价值", "金融资产",
        "资产重组", "重组收益", "债务重组", "一次性收益",
        "非经常性", "拆迁补偿", "股权转让", "投资收益",
        "低基数", "扭亏为盈", "出售", "处置",
    ])
    # 扣非净利润占比阈值：扣非占比<此值则降级
    min_deducted_ratio: float = 0.7
    # 营收增长最低要求
    min_revenue_growth: float = -10.0  # 允许小幅下滑


# ──────────────────────────────────────────────
# 基本面评分权重配置
# ──────────────────────────────────────────────
@dataclass
class EarningsScoreConfig:
    """基本面评分配置"""
    revenue_growth_weight: float = 0.15
    deducted_profit_growth_weight: float = 0.20
    net_profit_growth_weight: float = 0.10
    gross_margin_weight: float = 0.10
    roe_weight: float = 0.10
    roic_weight: float = 0.10
    ocf_ratio_weight: float = 0.10
    debt_ratio_weight: float = 0.05
    consecutive_improve_bonus: float = 5.0
    consecutive_accelerate_bonus: float = 8.0
    main_biz_ratio_weight: float = 0.10

    # 阈值
    revenue_growth_thresholds: list = field(default_factory=lambda: [
        (50, 100), (20, 85), (0, 65), (-10, 40), (-999, 0)
    ])
    deducted_growth_thresholds: list = field(default_factory=lambda: [
        (100, 100), (50, 90), (20, 75), (0, 55), (-999, 0)
    ])
    gross_margin_thresholds: list = field(default_factory=lambda: [
        (60, 100), (40, 85), (20, 65), (10, 45), (-999, 20)
    ])
    roe_thresholds: list = field(default_factory=lambda: [
        (20, 100), (15, 85), (10, 65), (5, 45), (-999, 20)
    ])
    roic_thresholds: list = field(default_factory=lambda: [
        (15, 100), (10, 85), (5, 65), (0, 40), (-999, 10)
    ])
    ocf_ratio_thresholds: list = field(default_factory=lambda: [
        (100, 100), (70, 85), (50, 70), (30, 50), (-999, 20)
    ])
    debt_ratio_thresholds: list = field(default_factory=lambda: [
        (30, 100), (40, 90), (50, 75), (60, 55), (70, 35), (100, 15)
    ])
    main_biz_ratio_thresholds: list = field(default_factory=lambda: [
        (95, 100), (80, 85), (60, 65), (40, 40), (-999, 20)
    ])


# ──────────────────────────────────────────────
# 机构资金评分配置
# ──────────────────────────────────────────────
@dataclass
class InstitutionScoreConfig:
    short_term_weight: float = 0.25   # 近5日
    mid_term_weight: float = 0.35     # 近10日
    long_term_weight: float = 0.15    # 近20日
    breakout_weight: float = 0.10
    north_flow_weight: float = 0.10
    fund_holding_weight: float = 0.05


# ──────────────────────────────────────────────
# 筹码评分配置
# ──────────────────────────────────────────────
@dataclass
class ChipScoreConfig:
    profit_ratio_weight: float = 0.20
    avg_cost_diff_weight: float = 0.15
    concentration_weight: float = 0.20
    peak_strength_weight: float = 0.15
    lockup_ratio_weight: float = 0.15
    cost_rise_speed_weight: float = 0.15


# ──────────────────────────────────────────────
# 趋势评分配置
# ──────────────────────────────────────────────
@dataclass
class TrendScoreConfig:
    alpha_weight: float = 0.15
    relative_alpha_weight: float = 0.10
    trend_weight: float = 0.15
    momentum_weight: float = 0.15
    ma_alignment_weight: float = 0.10
    new_high_count_weight: float = 0.10
    atr_weight: float = 0.05
    volatility_weight: float = 0.05
    beta_weight: float = 0.05
    relative_strength_weight: float = 0.10


# ──────────────────────────────────────────────
# 行业评分配置
# ──────────────────────────────────────────────
@dataclass
class IndustryScoreConfig:
    top3_score: float = 100.0
    top5_score: float = 90.0
    top10_score: float = 80.0
    normal_score: float = 60.0
    cold_score: float = 40.0
    top3_max_rank: int = 3
    top5_max_rank: int = 5
    top10_max_rank: int = 10


# ──────────────────────────────────────────────
# 预期差评分配置
# ──────────────────────────────────────────────
@dataclass
class ExpectationGapConfig:
    positive_surprise_score: float = 100.0
    neutral_score: float = 60.0
    negative_surprise_score: float = 20.0
    surprise_threshold_pct: float = 10.0  # 超过预期10%为正向惊喜


# ──────────────────────────────────────────────
# 最终评分权重配置
# ──────────────────────────────────────────────
@dataclass
class FinalScoreConfig:
    """ELD 最终评分权重 = ELS(25/20/15/10/10/5/5/5/5)"""
    event_quality_weight: float = 0.25
    earnings_weight: float = 0.20
    institution_weight: float = 0.15
    chip_weight: float = 0.10
    trend_weight: float = 0.10
    industry_weight: float = 0.05
    freshness_weight: float = 0.05
    expectation_gap_weight: float = 0.05
    similarity_weight: float = 0.05


# ──────────────────────────────────────────────
# 报告配置
# ──────────────────────────────────────────────
@dataclass
class ReportConfig:
    top_n: int = 50
    include_detail: bool = True
    output_markdown: bool = True
    output_csv: bool = True
    output_sqlite: bool = True
    output_json: bool = True
    markdown_template: str = "eld_report_template.md"


# ──────────────────────────────────────────────
# 统一配置容器
# ──────────────────────────────────────────────
@dataclass
class Config:
    """系统统一配置容器"""
    global_: GlobalConfig = field(default_factory=GlobalConfig)
    tushare: TushareConfig = field(default_factory=TushareConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    event_filter: EventFilterConfig = field(default_factory=EventFilterConfig)
    earnings: EarningsScoreConfig = field(default_factory=EarningsScoreConfig)
    institution: InstitutionScoreConfig = field(default_factory=InstitutionScoreConfig)
    chip: ChipScoreConfig = field(default_factory=ChipScoreConfig)
    trend: TrendScoreConfig = field(default_factory=TrendScoreConfig)
    industry: IndustryScoreConfig = field(default_factory=IndustryScoreConfig)
    expectation_gap: ExpectationGapConfig = field(default_factory=ExpectationGapConfig)
    final_score: FinalScoreConfig = field(default_factory=FinalScoreConfig)
    report: ReportConfig = field(default_factory=ReportConfig)


# 全局单例
_CONFIG: Optional[Config] = None


def get_config() -> Config:
    """获取全局配置单例"""
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = Config()
    return _CONFIG


def reload_config() -> Config:
    """重新加载配置"""
    global _CONFIG
    _CONFIG = Config()
    return _CONFIG
