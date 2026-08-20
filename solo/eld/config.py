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
    cache_dir: str = os.getenv("ELD_CACHE_DIR", "")  # 留空则由 CacheConfig.__post_init__ 解析
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
    """缓存配置（V2: 统一到 cache_config.CACHE_ROOT，消除相对路径分裂）"""
    sqlite_enabled: bool = True
    csv_enabled: bool = True
    expire_hours: int = 6
    # 统一缓存根目录：优先从 cache_config 读取，环境变量 ELD_CACHE_DIR 可覆盖
    _cache_root: str = os.getenv("ELD_CACHE_DIR", "")
    sqlite_db: str = "eld_cache.sqlite"
    sqlite_cache_dir: str = ""  # 运行时由 _resolve_cache_dir() 填充
    csv_cache_dir: str = ""
    incremental_update: bool = True

    def __post_init__(self):
        if not self.sqlite_cache_dir or not self.csv_cache_dir:
            root = self._cache_root
            if not root:
                try:
                    from cache_config import CACHE_ROOT
                    root = CACHE_ROOT
                except Exception:
                    root = "d:/mystock/cache_daily"
            self.sqlite_cache_dir = root
            self.csv_cache_dir = os.path.join(root, "eld_csv")


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
    # 预告利润增速最低要求（p_change_min 平均值 < 此值则降级）
    min_forecast_growth: float = 30.0  # 预告利润增速≥30%才保留


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
# 预期差评分配置（原版保持兼容）
# ──────────────────────────────────────────────
@dataclass
class ExpectationGapConfig:
    positive_surprise_score: float = 100.0
    neutral_score: float = 60.0
    negative_surprise_score: float = 20.0
    surprise_threshold_pct: float = 10.0


# ──────────────────────────────────────────────
# 预期差引擎 V2 配置（代理预期模型）
# ──────────────────────────────────────────────
@dataclass
class ExpectationGapV2Config:
    """预期差引擎 V2 配置 — 代理预期模型"""
    # 行业基准：回溯季度数
    industry_lookback_quarters: int = 4
    # 行业采样公司数上限
    max_industry_samples: int = 20

    # gap >100% → 90-100分
    gap_over_100_score: float = 95.0
    # gap 50%-100% → 75-90分
    gap_50_100_score_min: float = 75.0
    gap_50_100_score_max: float = 90.0
    # gap 20%-50% → 60-75分
    gap_20_50_score_min: float = 60.0
    gap_20_50_score_max: float = 75.0
    # gap <20% → 40-60分
    gap_under_20_score_min: float = 40.0
    gap_under_20_score_max: float = 60.0

    # 加速度加分
    acceleration_bonus_weight: float = 0.10  # 加速度占总分10%
    max_acceleration_bonus: float = 10.0

    # 分位数选择：mean 或 median
    benchmark_stat: str = "median"


# ──────────────────────────────────────────────
# 机构吸筹引擎配置
# ──────────────────────────────────────────────
@dataclass
class InstitutionAccumulationConfig:
    """机构吸筹检测配置"""
    # 权重
    fund_flow_weight: float = 0.40      # 资金趋势权重
    volume_price_weight: float = 0.30   # 量价结构权重
    chip_change_weight: float = 0.30    # 筹码变化权重

    # 资金指标
    short_term_days: int = 5
    mid_term_days: int = 10
    long_term_days: int = 20

    # 量价指标
    volume_trend_days: int = 20
    up_volume_threshold: float = 1.2    # 上涨日量比阈值
    down_shrink_threshold: float = 0.8  # 下跌日缩量阈值
    turnover_change_days: int = 10

    # 筹码指标
    chip_lookback_days: int = 10

    # 状态评分阈值
    accumulation_score_threshold: float = 60.0  # 吸筹
    wash_score_threshold: float = 40.0          # 洗盘
    launch_score_threshold: float = 75.0        # 启动
    accelerate_score_threshold: float = 85.0    # 加速
    distribute_score_threshold: float = 30.0    # 派发


# ──────────────────────────────────────────────
# 业绩回踩买点引擎配置
# ──────────────────────────────────────────────
@dataclass
class EarningsBuyPointConfig:
    """业绩回踩买点检测配置"""
    # 公告时间窗口
    min_days_since_announce: int = 5
    max_days_since_announce: int = 20

    # 趋势要求
    ma_period: int = 20  # MA20
    ma10_period: int = 10
    ma5_period: int = 5

    # 回撤要求
    max_pullback_from_high_pct: float = 10.0  # 距离公告后高点<10%

    # 缩量要求
    max_volume_ratio: float = 0.6  # 量比<0.6

    # Alpha要求
    min_alpha: float = 70.0

    # 利好兑现检测
    pre_announce_runup_days: int = 20     # 公告前回看天数
    pre_announce_runup_threshold: float = 20.0  # 公告前涨幅≥此值视为利好兑现风险
    max_post_announce_decline: float = -5.0     # 公告后最大允许跌幅（不含首日）
    post_announce_decline_days: int = 5          # 公告后观察天数

    # 趋势Alpha兜底
    trend_alpha_floor: float = 60.0       # 趋势Alpha<此值，BUY降级为WATCH

    # ── 最佳买点信号（V3） ──
    # 乖离率控制（(close-ma20)/ma20*100）
    bias_chase_threshold: float = 15.0    # 乖离>15% = 追高风险，BUY降级WATCH
    bias_optimal_min: float = -2.0        # 最佳买入区：乖离 -2%~8%（回测2026-01~07 ELD场景内5~10优于0~5）
    bias_optimal_max: float = 8.0
    bias_ok_max: float = 10.0             # 乖离≤10% 允许WATCH

    # 市场环境门控（回测结论：大盘<MA20 期间买点信号负期望，应整体降级）
    market_gate_enabled: bool = True      # 开关：大盘(沪深300)<MA20 时 BUY 降级 WATCH
    market_gate_benchmark: str = "000300.SH"  # 大盘基准指数

    # ── 正式报告阶段规则（石药创新 2026-08 案例提炼：预告后→正式报告前走势） ──
    # 预告已把业绩区间打明牌，报告披露日前后走势特征：
    #   a) 报告披露前 N 个交易日（含披露日当日）资金抢跑，披露后常平盘/回落 → 追高无意义
    #   b) 报告披露后 M 个交易日内为"利好落地观察期"，宜等回踩企稳再介入
    # 仅在拿到正式披露日（financial.report_date）时生效；缺失则维持原逻辑
    report_pre_days: int = 3         # 披露前 N 个交易日(含披露日) = 抢跑期，BUY 降级 WATCH
    report_post_days: int = 2        # 披露后 N 个交易日内 = 落地观察期，BUY 降级 WATCH
    report_stage_downgrade: bool = True  # 总开关：抢跑/落地期整体降级 BUY
    report_stage_panic_ignore: bool = False  # 落地观察期内连涨追高(当日涨幅>10%)直接 IGNORE

    # 买点质量评分权重（0-100）
    quality_bias_weight: float = 0.25     # 乖离合理度
    quality_pullback_weight: float = 0.25 # 回踩深度（贴近MA10/MA20）
    quality_volume_weight: float = 0.20   # 缩量程度
    quality_stabilize_weight: float = 0.15 # 企稳确认（小阴小阳+MACD收敛）
    quality_institution_weight: float = 0.15 # 机构状态

    # 质量分阈值
    quality_buy_threshold: float = 80.0   # ≥80 强买点BUY（与V2≥55联合）
    quality_watch_threshold: float = 50.0 # ≥50 WATCH

    # BUY 所需最低 V2 分（在final_score层校验）
    v2_min_for_buy: float = 55.0          # 质量分高但V2<55时BUY降级WATCH

    # 评分映射
    buy_score_threshold: float = 75.0   # BUY
    watch_score_threshold: float = 50.0  # WATCH


# ──────────────────────────────────────────────
# 最终评分权重配置（ELD V2）
# ──────────────────────────────────────────────
@dataclass
class FinalScoreConfig:
    """ELD V2 最终评分权重

    V2 (6维度): 30/20/20/15/10/5 = 1.00
    V1 (9维度): 25/20/15/10/10/5/5/5/5 = 1.00（v1_* 前缀）
    """
    # ── V2 新权重（compute_els_v2 使用） ──
    event_quality_weight: float = 0.30     # 事件质量 30%
    expectation_gap_weight: float = 0.20   # 预期差 20%
    trend_weight: float = 0.20             # 趋势Alpha 20%
    institution_weight: float = 0.15       # 机构资金 15%
    industry_weight: float = 0.10          # 主题 10%
    etf_weight: float = 0.05               # ETF 5%

    # ── V1 权重（compute_els V1 使用） ──
    v1_event_quality_weight: float = 0.25
    v1_earnings_weight: float = 0.20
    v1_institution_weight: float = 0.15
    v1_chip_weight: float = 0.10
    v1_trend_weight: float = 0.10
    v1_industry_weight: float = 0.05
    v1_freshness_weight: float = 0.05
    v1_expectation_gap_weight: float = 0.05
    v1_similarity_weight: float = 0.05


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
    expectation_gap_v2: ExpectationGapV2Config = field(default_factory=ExpectationGapV2Config)
    institution_accumulation: InstitutionAccumulationConfig = field(default_factory=InstitutionAccumulationConfig)
    earnings_buy_point: EarningsBuyPointConfig = field(default_factory=EarningsBuyPointConfig)
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
