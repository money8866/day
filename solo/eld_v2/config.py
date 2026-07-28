"""
ELD V3 系统配置 - 所有权重/阈值/参数集中管理

遵循以下原则：
1. 禁止 Magic Number - 所有数字必须从 config 读取
2. 环境变量覆盖 - 支持通过 ELD_ 前缀环境变量覆盖
3. 线程安全 - 不可变配置对象
4. 全局单例 - get_config() 统一访问入口
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Optional


# ── 全局配置 ──
@dataclass
class GlobalConfig:
    debug: bool = False
    log_level: str = "INFO"
    data_dir: str = "d:/mystock"
    output_dir: str = "d:/mystock/report_daily"
    cache_dir: str = "cache/eld_v3"
    max_workers: int = 8
    target_date: Optional[str] = None
    
    def __post_init__(self):
        self.debug = os.getenv("ELD_DEBUG", "false").lower() == "true"
        self.log_level = os.getenv("ELD_LOG_LEVEL", "INFO")
        self.max_workers = int(os.getenv("ELD_MAX_WORKERS", "8"))
        td = os.getenv("ELD_TARGET_DATE")
        if td:
            self.target_date = td


# ── Tushare 配置 ──
@dataclass
class TushareConfig:
    token: str = ""
    timeout: int = 30
    retry_count: int = 3
    retry_delay: float = 1.0
    rate_limit_ms: int = 120
    
    def __post_init__(self):
        self.token = os.getenv("TUSHARE_TOKEN", "") or os.getenv("TUSHARE_TOKEN", "")


# ── 缓存配置 ──
@dataclass
class CacheConfig:
    sqlite_enabled: bool = True
    csv_enabled: bool = True
    expire_hours: int = 6
    sqlite_db: str = "eld_v3_cache.sqlite"
    parquet_cache_dir: str = "cache/eld_v3/parquet"


# ══════════════════════════════════════════
# 第一层: Event Quality Engine (权重20%)
# ══════════════════════════════════════════
@dataclass
class EventQualityConfig:
    weight: float = 0.20
    
    # 排除关键词
    exclude_keywords: list = field(default_factory=lambda: [
        "卖资产", "政府补贴", "补贴", "公允价值变动", "金融资产",
        "资产重组", "重组收益", "债务重组", "一次性收益",
        "非经常性损益", "拆迁补偿", "股权转让", "投资收益",
        "低基数", "扭亏为盈", "出售", "处置", "债务豁免",
    ])
    
    # 扣非占比阈值
    min_deducted_ratio: float = 0.70
    
    # 营收增长最低要求
    min_revenue_growth: float = -10.0
    
    # 扣分规则
    non_recurring_penalty: float = 20.0
    low_deducted_penalty: float = 15.0
    negative_revenue_penalty: float = 10.0
    mild_negative_revenue_penalty: float = 5.0
    low_forecast_growth_penalty_per_pct: float = 0.5
    max_forecast_penalty: float = 40.0
    
    # 连续改善加分
    consecutive_improve_bonus: float = 5.0
    consecutive_accelerate_bonus: float = 8.0
    
    # 毛利率改善加分
    gross_margin_improve_bonus: float = 5.0
    gross_margin_improve_threshold: float = 3.0  # 百分点
    
    # ROE改善加分
    roe_improve_bonus: float = 5.0
    roe_improve_threshold: float = 2.0  # 百分点


# ══════════════════════════════════════════
# 第二层: Expectation Gap Engine (权重15%)
# ══════════════════════════════════════════
@dataclass
class ExpectationGapConfig:
    weight: float = 0.15
    
    # 评分映射
    large_positive_score: float = 100.0  # 大幅超预期
    positive_score: float = 80.0
    neutral_score: float = 55.0
    negative_score: float = 25.0
    large_negative_score: float = 0.0
    
    # 阈值
    large_positive_threshold: float = 30.0   # 超预期30%以上
    positive_threshold: float = 10.0          # 超预期10%以上
    negative_threshold: float = -10.0         # 低于预期10%以上
    large_negative_threshold: float = -30.0   # 低于预期30%以上
    
    # 代理预期计算参数
    acceleration_weight: float = 0.40          # 加速度权重
    industry_gap_weight: float = 0.40          # 行业缺口权重
    stability_weight: float = 0.20             # 稳定性权重
    
    # 历史季度数
    lookback_quarters: int = 4


# ══════════════════════════════════════════
# 第三层: Institution Accumulation Engine (权重20%)
# ══════════════════════════════════════════
@dataclass
class InstitutionAccumulationConfig:
    weight: float = 0.20
    
    # 资金维度权重
    short_term_flow_weight: float = 0.15    # 近5日
    mid_term_flow_weight: float = 0.25      # 近10日
    long_term_flow_weight: float = 0.15     # 近20日
    volume_trend_weight: float = 0.10       # 成交额趋势
    volume_ratio_weight: float = 0.10       # 量比
    turnover_rate_weight: float = 0.05      # 换手率
    north_flow_weight: float = 0.10         # 北向资金
    chip_movement_weight: float = 0.10      # 筹码峰移动
    
    # 大单净流入评分阈值
    large_inflow_threshold: float = 0.15     # 15%→100分
    medium_inflow_threshold: float = 0.08    # 8%→75分
    small_inflow_threshold: float = 0.03     # 3%→50分
    neutral_inflow_threshold: float = 0.0    # 0%→30分
    negative_inflow_threshold: float = -0.05 # -5%→10分
    
    # 北向资金变化阈值
    north_increase_threshold: float = 0.05   # 5%→加分
    north_decrease_threshold: float = -0.05  # -5%→减分
    
    # 状态识别参数
    accumulation_volume_ratio_min: float = 0.8    # 吸筹量比下限
    accumulation_volume_ratio_max: float = 1.3    # 吸筹量比上限
    testing_volume_ratio_min: float = 0.5         # 试盘量比下限
    testing_volume_ratio_max: float = 0.9         # 试盘量比上限
    washing_volume_ratio_min: float = 0.6         # 洗盘量比下限
    washing_volume_ratio_max: float = 1.0         # 洗盘量比上限
    adding_volume_ratio_min: float = 1.2          # 加仓量比下限
    distributing_volume_ratio_min: float = 1.5    # 派发量比下限


# ══════════════════════════════════════════
# 第四层: Chip Structure Engine (权重10%)
# ══════════════════════════════════════════
@dataclass
class ChipStructureConfig:
    weight: float = 0.10
    
    # 子因子权重
    profit_ratio_weight: float = 0.20
    cost_deviation_weight: float = 0.15
    concentration_weight: float = 0.20
    peak_strength_weight: float = 0.15
    lockup_ratio_weight: float = 0.15
    cost_rise_speed_weight: float = 0.15
    
    # 获利盘评分阈值
    profit_ratio_excellent: float = 0.80    # ≥80%→100分
    profit_ratio_good: float = 0.60         # ≥60%→80分
    profit_ratio_fair: float = 0.40         # ≥40%→60分
    profit_ratio_weak: float = 0.20         # ≥20%→40分
    profit_ratio_poor: float = 0.10         # ≥10%→20分
    
    # 成本偏差理想区间
    cost_deviation_low: float = 0.05        # 5%
    cost_deviation_high: float = 0.30       # 30%
    
    # 集中度阈值（越低越好）
    concentration_excellent: float = 0.10
    concentration_good: float = 0.15
    concentration_fair: float = 0.20
    concentration_weak: float = 0.30


# ══════════════════════════════════════════
# 第五层: Trend Quality Engine (权重10%)
# ══════════════════════════════════════════
@dataclass
class TrendQualityConfig:
    weight: float = 0.10
    
    # 子因子权重
    alpha_weight: float = 0.12
    relative_alpha_weight: float = 0.08
    ma_alignment_weight: float = 0.15
    momentum_weight: float = 0.12
    new_high_weight: float = 0.08
    atr_weight: float = 0.05
    volatility_weight: float = 0.05
    beta_weight: float = 0.05
    relative_strength_weight: float = 0.10
    ma_slope_weight: float = 0.10
    volume_price_weight: float = 0.10
    
    # 均线周期
    ma_periods: list = field(default_factory=lambda: [5, 10, 20, 60, 120, 250])
    
    # 动量周期
    momentum_period: int = 20
    new_high_lookback: int = 60
    new_high_window: int = 20
    
    # 最小数据要求
    min_days_required: int = 10


# ══════════════════════════════════════════
# 第六层: Catalyst Resonance Engine (权重5%)
# ══════════════════════════════════════════
@dataclass
class CatalystResonanceConfig:
    weight: float = 0.05
    
    # 催化剂列表及权重
    catalysts: dict = field(default_factory=lambda: {
        "业绩预增": 15,
        "AI": 20,
        "机器人": 18,
        "人形机器人": 20,
        "创新药": 15,
        "军工": 12,
        "低空经济": 18,
        "国产替代": 15,
        "半导体": 18,
        "芯片": 18,
        "涨价": 10,
        "新能源": 10,
        "政策": 8,
        "订单": 12,
        "并购重组": 10,
        "股权激励": 8,
        "回购": 6,
    })
    
    # 浓度要求 - 主题需在 theme 或概念中匹配到才生效
    min_catalyst_count: int = 1      # 至少1个催化剂
    max_catalyst_score: float = 100.0
    theme_boost_per_catalyst: float = 15.0  # 每个额外催化剂加分


# ══════════════════════════════════════════
# 第七层: Historical Similarity Engine (权重10%)
# ══════════════════════════════════════════
@dataclass
class HistoricalSimilarityConfig:
    weight: float = 0.10
    
    # 特征向量维度
    feature_columns: list = field(default_factory=lambda: [
        "industry_code",       # 行业
        "log_market_cap",      # 对数市值
        "revenue_growth",      # 收入增速
        "profit_growth",       # 利润增速
        "roe",                 # ROE
        "roic",                # ROIC
        "alpha_20d",           # 20日Alpha
        "relative_alpha",      # 相对Alpha
        "profit_ratio",        # 获利盘比例
        "institution_flow_10d",# 10日机构资金流
        "turnover_rate",       # 换手率
        "atr_ratio",           # ATR比率
        "log_total_mv",        # 对数总市值
    ])
    
    # 相似度阈值
    min_similarity: float = 0.50
    top_n: int = 10
    
    # 历史牛股库参数
    history_lookback_years: int = 10
    top_n_stocks: int = 1000
    forward_days_60: int = 60
    forward_days_120: int = 120


# ══════════════════════════════════════════
# 第八层: Crowding Risk Engine (权重5%, 反向)
# ══════════════════════════════════════════
@dataclass
class CrowdingRiskConfig:
    weight: float = 0.05
    is_reverse: bool = True  # 反向指标
    
    # 各因子阈值
    high_risk_20d_return: float = 30.0       # 20日涨幅>30%→高风险
    medium_risk_20d_return: float = 20.0     # 20日涨幅>20%→中风险
    high_risk_turnover: float = 15.0         # 换手率>15%→高风险
    medium_risk_turnover: float = 10.0       # 换手率>10%→中风险
    high_risk_limit_up_count: int = 3        # 近20日涨停次数>3→高风险
    high_risk_dragon_tiger_count: int = 3    # 龙虎榜次数>3→高风险
    high_risk_volume_ratio: float = 3.0      # 量比>3→高风险
    high_risk_margin_ratio: float = 0.50     # 融资余额/流通市值>50%→高风险
    
    # 评分映射
    high_risk_score: float = 0.0
    medium_risk_score: float = 50.0
    low_risk_score: float = 100.0


# ══════════════════════════════════════════
# 第九层: Announcement Timing Engine (权重5%)
# ══════════════════════════════════════════
@dataclass
class AnnouncementTimingConfig:
    weight: float = 0.05
    
    # 各阶段评分
    announcement_score: float = 50.0    # 刚公告
    breakout_score: float = 80.0        # 突破
    pullback_score: float = 70.0        # 回踩
    base_score: float = 60.0            # 平台整理
    second_breakout_score: float = 90.0 # 二波突破
    trend_score: float = 75.0           # 趋势中
    
    # 阶段判定参数
    breakout_volume_ratio: float = 1.3      # 突破量比
    breakout_gain: float = 3.0              # 突破涨幅%
    pullback_max_depth: float = 0.05        # 回踩最大深度5%
    base_formation_days: int = 10           # 平台形成至少10天
    second_breakout_days_min: int = 5       # 二波至少间隔5天
    second_breakout_days_max: int = 60      # 二波最常间隔60天


# ══════════════════════════════════════════
# 第十层: Buy Point State Machine
# ══════════════════════════════════════════
@dataclass
class BuyPointConfig:
    # 入场信号阈值
    pullback_ma20_tolerance: float = 0.03    # 回踩MA20容忍度3%
    breakout_volume_ratio: float = 1.3       # 突破量比
    breakout_gain_min: float = 3.0           # 突破最小涨幅3%
    second_breakout_volume_ratio: float = 1.2
    second_breakout_gain_min: float = 2.0
    
    # 止损止盈
    stop_loss_atr_multiple: float = 2.0
    take_profit_atr_multiple: float = 3.0
    max_loss_pct: float = 0.07              # 最大亏损7%
    target_gain_pct: float = 0.15           # 目标收益15%
    
    # 持仓建议
    min_hold_days: int = 5
    max_hold_days: int = 120
    expected_hold_days_breakout: int = 20
    expected_hold_days_trend: int = 60
    expected_hold_days_base: int = 30
    
    # 仓位建议
    max_position_pct: float = 0.20          # 单票最大仓位20%
    init_position_pct: float = 0.05         # 试仓5%
    add_position_pct: float = 0.10          # 加仓10%


# ══════════════════════════════════════════
# 最终评分权重汇总
# ══════════════════════════════════════════
@dataclass 
class IESWeights:
    """IES 十层权重 - 总和必须为1.0"""
    event_quality: float = 0.20
    expectation_gap: float = 0.15
    institution_accumulation: float = 0.20
    chip_health: float = 0.10
    trend_quality: float = 0.10
    catalyst_resonance: float = 0.05
    historical_similarity: float = 0.10
    crowding_risk: float = 0.05
    announcement_timing: float = 0.05
    
    def __post_init__(self):
        total = sum([
            self.event_quality,
            self.expectation_gap,
            self.institution_accumulation,
            self.chip_health,
            self.trend_quality,
            self.catalyst_resonance,
            self.historical_similarity,
            self.crowding_risk,
            self.announcement_timing,
        ])
        if abs(total - 1.0) > 0.001:
            import warnings
            warnings.warn(f"IES weights sum to {total:.3f}, expected 1.0")


# ── 市场状态 ──
@dataclass
class MarketConfig:
    bull_threshold: float = 0.5       # 近20日平均涨幅>0.5%
    recovery_threshold: float = 0.0   # 近20日平均涨幅>0%
    weak_threshold: float = -0.3      # 近20日平均涨幅>-0.3%
    bull_volatility_max: float = 1.5  # 波动率<1.5%
    
    bull_multiplier: float = 1.05
    recovery_multiplier: float = 1.00
    weak_multiplier: float = 0.85
    bear_multiplier: float = 0.65


# ── 报告配置 ──
@dataclass
class ReportConfig:
    top_n: int = 50
    include_detail: bool = True
    output_markdown: bool = True
    output_csv: bool = True
    output_sqlite: bool = True
    output_json: bool = True


# ── 统一配置容器 ──
@dataclass
class Config:
    global_: GlobalConfig = field(default_factory=GlobalConfig)
    tushare: TushareConfig = field(default_factory=TushareConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    market: MarketConfig = field(default_factory=MarketConfig)
    ies: IESWeights = field(default_factory=IESWeights)
    event_quality: EventQualityConfig = field(default_factory=EventQualityConfig)
    expectation_gap: ExpectationGapConfig = field(default_factory=ExpectationGapConfig)
    institution: InstitutionAccumulationConfig = field(default_factory=InstitutionAccumulationConfig)
    chip: ChipStructureConfig = field(default_factory=ChipStructureConfig)
    trend: TrendQualityConfig = field(default_factory=TrendQualityConfig)
    catalyst: CatalystResonanceConfig = field(default_factory=CatalystResonanceConfig)
    similarity: HistoricalSimilarityConfig = field(default_factory=HistoricalSimilarityConfig)
    crowding: CrowdingRiskConfig = field(default_factory=CrowdingRiskConfig)
    timing: AnnouncementTimingConfig = field(default_factory=AnnouncementTimingConfig)
    buy_point: BuyPointConfig = field(default_factory=BuyPointConfig)
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
