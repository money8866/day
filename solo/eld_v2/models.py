"""
ELD V3 数据模型 - 所有评分引擎的输入/输出数据定义

所有模型使用 dataclass + 类型注解。
禁止在业务代码中直接构造字典作为结果传递。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ═══════════════════════════════════════════════
# 枚举类型
# ═══════════════════════════════════════════════

class InstitutionState(str, Enum):
    """机构状态"""
    ACCUMULATION = "吸筹"
    TESTING = "试盘"
    WASHING = "洗盘"
    ADDING = "加仓"
    DISTRIBUTION = "派发"
    NEUTRAL = "中性"


class CrowdingLevel(str, Enum):
    HIGH = "高"
    MEDIUM = "中"
    LOW = "低"


class MarketRegime(str, Enum):
    BULL = "bull"
    RECOVERY = "recovery"
    WEAK = "weak"
    BEAR = "bear"
    UNKNOWN = "unknown"


class BuyPointState(str, Enum):
    """买点状态机"""
    ANNOUNCEMENT = "Announcement"
    BREAKOUT = "Breakout"
    FIRST_PULLBACK = "First Pullback"
    BASE_BUILDING = "Base Building"
    SECOND_BREAKOUT = "Second Breakout"
    MAIN_TREND = "Main Trend"
    DISTRIBUTION = "Distribution"
    NONE = "NONE"


class Recommendation(str, Enum):
    WATCH = "观察"
    WAIT_PULLBACK = "等待回踩"
    TRIAL = "试仓"
    ADD = "加仓"
    HOLD = "持有"
    REDUCE = "减仓"
    SELL = "卖出"


class RiskLevel(str, Enum):
    LOW = "低"
    MEDIUM = "中"
    HIGH = "高"


class SurpriseType(str, Enum):
    LARGE_POSITIVE = "大幅超预期"
    POSITIVE = "超预期"
    NEUTRAL = "符合预期"
    NEGATIVE = "低于预期"
    LARGE_NEGATIVE = "大幅低于预期"
    UNKNOWN = "未知"


class TimingStage(str, Enum):
    ANNOUNCEMENT = "公告期"
    BREAKOUT = "突破期"
    PULLBACK = "回踩期"
    BASE = "平台整理期"
    SECOND_BREAKOUT = "二波突破期"
    TREND = "趋势延续期"


# ═══════════════════════════════════════════════
# 第一层: Event Quality Engine 输出
# ═══════════════════════════════════════════════

@dataclass
class EventQualityResult:
    score: float = 0.0
    stars: int = 0          # 1-5星
    has_non_recurring: bool = False
    non_recurring_items: list[str] = field(default_factory=list)
    deducted_ratio: float = 0.0
    revenue_growth: float = 0.0
    deducted_growth: float = 0.0
    gross_margin_change: float = 0.0
    roe_change: float = 0.0
    consecutive_improve: bool = False
    consecutive_accelerate: bool = False
    logic: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════
# 第二层: Expectation Gap Engine 输出
# ═══════════════════════════════════════════════

@dataclass
class IndustryExpectation:
    """行业预期数据"""
    industry: str = ""
    mean_growth: float = 0.0
    median_growth: float = 0.0
    percentile_75: float = 0.0
    percentile_90: float = 0.0
    stock_count: int = 0


@dataclass
class ExpectationGapResult:
    score: float = 0.0
    surprise_type: SurpriseType = SurpriseType.UNKNOWN

    # 代理预期数据
    industry_mean_growth: float = 0.0
    industry_median_growth: float = 0.0
    company_growth: float = 0.0

    # 加速度
    revenue_acceleration: float = 0.0      # 收入加速度
    profit_acceleration: float = 0.0       # 利润加速度
    deducted_acceleration: float = 0.0     # 扣非加速度
    acceleration_score: float = 0.0

    # 稳定性
    growth_stability: float = 0.0          # 增长稳定性(方差倒数)
    stability_score: float = 0.0

    # 综合
    industry_gap: float = 0.0              # 行业缺口
    combined_gap: float = 0.0              # 综合缺口
    logic: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════
# 第三层: Institution Accumulation Engine 输出
# ═══════════════════════════════════════════════

@dataclass
class InstitutionAccumulationResult:
    score: float = 0.0

    # 资金流
    short_term_flow: float = 0.0     # 近5日净大单流入比
    mid_term_flow: float = 0.0       # 近10日
    long_term_flow: float = 0.0      # 近20日

    # 成交额
    volume_ma5: float = 0.0
    volume_ma10: float = 0.0
    volume_trend: float = 0.0        # 成交额趋势(正=放大)
    volume_ratio: float = 0.0        # 量比

    # 换手
    turnover_rate: float = 0.0
    turnover_change: float = 0.0     # 换手率变化

    # 北向
    north_flow_change: float = 0.0
    north_flow_score: float = 0.0

    # 筹码移动
    chip_peak_shift: float = 0.0     # 筹码峰移动
    avg_cost_change: float = 0.0     # 平均成本变化
    profit_ratio_change: float = 0.0 # 获利盘变化

    # 状态识别
    institution_state: InstitutionState = InstitutionState.NEUTRAL
    consecutive_inflow_days: int = 0
    volume_surge: bool = False
    volume_shrink: bool = False

    logic: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════
# 第四层: Chip Structure Engine 输出
# ═══════════════════════════════════════════════

@dataclass
class ChipStructureResult:
    score: float = 0.0

    # 获利盘
    profit_ratio: float = 0.0
    profit_ratio_score: float = 0.0

    # 成本
    avg_cost: float = 0.0
    current_price: float = 0.0
    cost_deviation: float = 0.0        # 价格偏离成本
    cost_deviation_score: float = 0.0

    # 集中度
    concentration: float = 0.0         # 成本集中度(越小越好)
    concentration_score: float = 0.0

    # 峰强度
    peak_price: float = 0.0
    peak_strength: float = 0.0         # 成本峰强度
    peak_strength_score: float = 0.0

    # 锁仓
    lockup_ratio: float = 0.0
    lockup_score: float = 0.0

    # 成本抬升
    cost_rise_speed: float = 0.0
    cost_rise_score: float = 0.0

    # 综合健康度
    health_label: str = "健康"         # 健康/一般/差

    logic: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════
# 第五层: Trend Quality Engine 输出
# ═══════════════════════════════════════════════

@dataclass
class TrendQualityResult:
    score: float = 0.0

    alpha: float = 0.0
    relative_alpha: float = 0.0
    ma_alignment_score: float = 0.0  # 均线排列分
    ma_alignment_count: int = 0       # 多头排列对数
    momentum: float = 0.0
    momentum_score: float = 0.0

    new_high_count: int = 0
    new_high_score: float = 0.0

    atr_ratio: float = 0.0
    atr_score: float = 0.0

    volatility: float = 0.0
    volatility_score: float = 0.0

    beta: float = 0.0
    beta_score: float = 0.0

    relative_strength: float = 0.0
    rs_score: float = 0.0

    ma_slope_score: float = 0.0       # 均线斜率分
    volume_price_score: float = 0.0   # 量价配合分

    logic: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════
# 第六层: Catalyst Resonance Engine 输出
# ═══════════════════════════════════════════════

@dataclass
class CatalystResonanceResult:
    score: float = 0.0
    matched_catalysts: list[str] = field(default_factory=list)
    catalyst_count: int = 0
    catalyst_details: list[dict] = field(default_factory=list)  # [{name, score}]
    logic: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════
# 第七层: Historical Similarity Engine 输出
# ═══════════════════════════════════════════════

@dataclass
class SimilarStock:
    ts_code: str = ""
    name: str = ""
    similarity: float = 0.0
    forward_return_60d: float = 0.0
    forward_return_120d: float = 0.0
    industry: str = ""
    announce_date: str = ""


@dataclass
class HistoricalSimilarityResult:
    score: float = 0.0
    similar_stocks: list[SimilarStock] = field(default_factory=list)
    avg_forward_return_60d: float = 0.0
    avg_forward_return_120d: float = 0.0
    feature_vector: list[float] = field(default_factory=list)
    logic: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════
# 第八层: Crowding Risk Engine 输出
# ═══════════════════════════════════════════════

@dataclass
class CrowdingRiskResult:
    score: float = 0.0              # 高分=低拥挤(好)
    risk_level: CrowdingLevel = CrowdingLevel.LOW
    return_20d: float = 0.0
    turnover_rate: float = 0.0
    limit_up_count: int = 0
    dragon_tiger_count: int = 0
    volume_ratio: float = 0.0
    margin_ratio: float = 0.0
    logic: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════
# 第九层: Announcement Timing Engine 输出
# ═══════════════════════════════════════════════

@dataclass
class AnnouncementTimingResult:
    score: float = 0.0
    stage: TimingStage = TimingStage.ANNOUNCEMENT
    days_since_announcement: int = 0
    is_breakout: bool = False
    is_pullback: bool = False
    is_base: bool = False
    is_second_breakout: bool = False
    breakout_volume_ratio: float = 0.0
    pullback_depth: float = 0.0
    logic: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════
# 第十层: Buy Point State Machine 输出
# ═══════════════════════════════════════════════

@dataclass
class BuyPointResult:
    state: BuyPointState = BuyPointState.NONE
    recommendation: Recommendation = Recommendation.WATCH
    target_position: float = 0.0          # 目标仓位%
    expected_hold_days: int = 0
    risk_level: RiskLevel = RiskLevel.MEDIUM
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    entry_price_low: float = 0.0          # 低吸参考价
    entry_price_high: float = 0.0         # 突破参考价
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0
    atr: float = 0.0
    stars: int = 0                         # 0-5星
    logic: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════
# 市场数据
# ═══════════════════════════════════════════════

@dataclass
class MarketState:
    regime: MarketRegime = MarketRegime.UNKNOWN
    multiplier: float = 1.0
    score: float = 50.0
    risk_appetite: float = 50.0            # 风险偏好 0-100
    avg_change_20d: float = 0.0
    volatility_20d: float = 0.0
    logic: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════
# 最终评分结果
# ═══════════════════════════════════════════════

@dataclass
class IESResult:
    """单只股票的IES最终评分结果"""
    # 基本信息
    ts_code: str = ""
    name: str = ""
    industry: str = ""
    theme: str = ""
    announce_date: str = ""
    forecast_pct: float = 0.0

    # IES
    ies: float = 0.0              # Institutional Event Score (原始)
    final_score: float = 0.0      # 乘以市场乘数后
    rank: int = 0
    market_multiplier: float = 1.0

    # 十层结果
    event_quality: EventQualityResult = field(default_factory=EventQualityResult)
    expectation_gap: ExpectationGapResult = field(default_factory=ExpectationGapResult)
    institution: InstitutionAccumulationResult = field(default_factory=InstitutionAccumulationResult)
    chip: ChipStructureResult = field(default_factory=ChipStructureResult)
    trend: TrendQualityResult = field(default_factory=TrendQualityResult)
    catalyst: CatalystResonanceResult = field(default_factory=CatalystResonanceResult)
    similarity: HistoricalSimilarityResult = field(default_factory=HistoricalSimilarityResult)
    crowding: CrowdingRiskResult = field(default_factory=CrowdingRiskResult)
    timing: AnnouncementTimingResult = field(default_factory=AnnouncementTimingResult)
    buy_point: BuyPointResult = field(default_factory=BuyPointResult)

    # 汇总
    recommendation: Recommendation = Recommendation.WATCH
    risk_level: RiskLevel = RiskLevel.MEDIUM
    institution_state: InstitutionState = InstitutionState.NEUTRAL
    current_stage: TimingStage = TimingStage.ANNOUNCEMENT

    def to_dict(self) -> dict[str, Any]:
        """转为扁平字典用于报告输出"""
        return {
            "ts_code": self.ts_code,
            "name": self.name,
            "industry": self.industry,
            "theme": self.theme,
            "announce_date": self.announce_date,
            "forecast_pct": round(self.forecast_pct, 1),
            "ies": round(self.ies, 2),
            "final_score": round(self.final_score, 2),
            "rank": self.rank,
            "event_quality": round(self.event_quality.score, 1),
            "expectation_gap": round(self.expectation_gap.score, 1),
            "institution_score": round(self.institution.score, 1),
            "chip_score": round(self.chip.score, 1),
            "trend_score": round(self.trend.score, 1),
            "catalyst_score": round(self.catalyst.score, 1),
            "similarity_score": round(self.similarity.score, 1),
            "crowding_score": round(self.crowding.score, 1),
            "timing_score": round(self.timing.score, 1),
            "current_stage": self.current_stage.value,
            "institution_state": self.institution_state.value,
            "recommendation": self.recommendation.value,
            "risk_level": self.risk_level.value,
            "target_position": self.buy_point.target_position,
            "expected_hold_days": self.buy_point.expected_hold_days,
        }


# ═══════════════════════════════════════════════
# 汇总报告
# ═══════════════════════════════════════════════

@dataclass
class IESReport:
    """IES 系统完整报告"""
    run_date: str = ""
    total_stocks: int = 0
    filtered_stocks: int = 0
    market_regime: str = ""
    results: list[IESResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_date": self.run_date,
            "total_stocks": self.total_stocks,
            "filtered_stocks": self.filtered_stocks,
            "market_regime": self.market_regime,
            "results": [r.to_dict() for r in self.results],
        }


# ═══════════════════════════════════════════════
# 事件适配器 - 事件数据通用结构
# ═══════════════════════════════════════════════

@dataclass
class EventData:
    """通用事件数据结构 - 所有事件类型通过Adapter转为此格式"""
    event_type: str = ""              # 事件类型: forecast/express/income/order/contract/approval/...
    ts_code: str = ""
    announce_date: str = ""
    end_date: str = ""

    # 财务相关
    p_change_min: Optional[float] = None
    p_change_max: Optional[float] = None
    net_profit: Optional[float] = None
    deducted_profit: Optional[float] = None
    revenue: Optional[float] = None

    # 事件描述
    summary: str = ""
    keywords: list[str] = field(default_factory=list)

    # 扩展字段
    extra: dict[str, Any] = field(default_factory=dict)


# ═══════════════════════════════════════════════
# 历史牛股库条目
# ═══════════════════════════════════════════════

@dataclass
class HistoricalBullStock:
    """历史牛股条目"""
    ts_code: str = ""
    name: str = ""
    industry: str = ""
    announce_date: str = ""
    end_date: str = ""               # 报告期

    # 事件时特征
    market_cap: float = 0.0
    revenue_growth: float = 0.0
    profit_growth: float = 0.0
    roe: float = 0.0
    roic: float = 0.0
    alpha_20d: float = 0.0
    relative_alpha: float = 0.0
    profit_ratio: float = 0.0
    institution_flow_10d: float = 0.0
    turnover_rate: float = 0.0
    atr_ratio: float = 0.0
    total_mv: float = 0.0

    # 后续收益
    forward_return_60d: float = 0.0
    forward_return_120d: float = 0.0

    # 主题
    themes: list[str] = field(default_factory=list)
