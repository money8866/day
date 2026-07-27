"""V3 评分数据模型."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ETFTrendResult:
    """ETF趋势评分."""
    score: float = 0.0
    return_20d: float = 0.0
    return_60d: float = 0.0
    ema_20_pos: float = 0.0
    ema_60_pos: float = 0.0
    macd_direction: str = "flat"
    new_high_20d_count: int = 0
    new_high_60d_count: int = 0
    max_drawdown_20d: float = 0.0
    sharpe_20d: float = 0.0
    trend_direction: float = 0.0     # 趋势方向分 0~100 (由return/ema/macd/new_high合成)
    trend_quality: float = 0.0       # 趋势质量分 0~100 (由sharpe/drawdown/volume合成)
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ETFAccelResult:
    """ETF加速度评分."""
    score: float = 0.0
    slope_5: float = 0.0
    slope_10: float = 0.0
    slope_20: float = 0.0
    ema_short_long_diff: float = 0.0
    trend_second_deriv: float = 0.0
    volume_5d_growth: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BreadthResult:
    """扩散度评分."""
    score: float = 0.0
    up_ratio: float = 0.0
    new_high_20d_ratio: float = 0.0
    new_high_60d_ratio: float = 0.0
    volume_breakout_ratio: float = 0.0
    consecutive_up_ratio: float = 0.0
    limit_up_ratio: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LeaderResult:
    """龙头质量评分."""
    score: float = 0.0
    alpha_20: float = 0.0
    relative_strength: float = 0.0
    volume_score: float = 0.0
    institutional_money: float = 0.0
    trend_score: float = 0.0
    chip_score: float = 0.0
    new_high_score: float = 0.0
    top_leaders: List[str] = field(default_factory=list)
    persistent_leaders: List[str] = field(default_factory=list)  # 持续性龙头 (连续≥2天在TOP5)
    persistent_days: Dict[str, int] = field(default_factory=dict) # 各龙头持续天数
    zhongjun: List[str] = field(default_factory=list)            # 中军 (大市值+大成交额+稳定)
    zhongjun_days: Dict[str, int] = field(default_factory=dict)  # 各中军持续天数
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LeaderExpandResult:
    """龙头扩散评分."""
    score: float = 0.0
    leader_count_change: int = 0
    mid_cap_count_change: int = 0
    strong_stock_count_change: int = 0
    leader_quality_change: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RankMomentumResult:
    """排名动量评分."""
    score: float = 0.0
    rank_1d_ago: int = 0
    rank_3d_ago: int = 0
    rank_5d_ago: int = 0
    rank_10d_ago: int = 0
    rank_change_1d: int = 0
    rank_change_5d: int = 0
    rank_change_10d: int = 0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MoneyFlowResult:
    """资金流评分."""
    score: float = 0.0
    theme_amount_change: float = 0.0
    etf_amount_change: float = 0.0
    northbound_flow: float = 0.0
    main_net_inflow: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LifecycleResult:
    """生命周期判定."""
    stage: str = "birth"
    stage_bonus: int = 0
    etf_trend_score: float = 0.0
    etf_accel_score: float = 0.0
    breadth_score: float = 0.0
    leader_score: float = 0.0
    transition: Optional[TransitionResult] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ResonanceResult:
    """共振评分（加分项）. """
    score: float = 0.0
    etf_trend_met: bool = False
    etf_accel_met: bool = False
    leader_met: bool = False
    breadth_met: bool = False
    conditions_met: int = 0
    multiplier: float = 1.0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TransitionResult:
    """主题生命周期迁移检测结果 V2 — 6因子评分 + 2修正项."""
    direction: str = "STABLE"           # ACCELERATING/PEAKING/DECELERATING/DECLINING/BOTTOMING/RECOVERING/STABLE/STALLING
    direction_cn: str = "稳定"           # 中文描述
    strength: float = 0.0               # 迁移强度 0~100
    confidence: float = 0.0             # 置信度 0~1
    from_stage: str = ""                # 当前阶段
    to_stage: str = ""                  # 预测迁移目标阶段
    days_estimate: int = 0              # 预计迁移天数
    pre_rotate: bool = False            # 是否触发提前轮动信号

    # 6因子分
    proximity_score: float = 0.0        # 距下一阶段阈值距离分
    momentum_score: float = 0.0         # 加速度方向分
    confirmation_score: float = 0.0     # 扩散度/成交量确认分
    money_resonance_score: float = 0.0  # 资金共振分
    leader_health_score: float = 0.0    # 龙头健康度分
    regime_compat_score: float = 0.0    # 市场适配分

    # 修正项
    age_penalty: float = 0.0            # 热点老化惩罚 (0=无惩罚, 负值=降低迁移概率)
    macro_filter: float = 0.0           # 宏观过滤 (正=加分, 负=减分)
    age_penalty_reason: str = ""        # 老化惩罚原因
    macro_filter_reason: str = ""       # 宏观过滤原因

    # 诊断
    acceleration_trend: float = 0.0     # ETF加速度变化率
    breadth_trend: float = 0.0          # 扩散度变化趋势
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ThemeV3Score:
    """单个主题的V3完整评分."""
    theme_code: str = ""
    theme_name: str = ""
    trade_date: str = ""
    rank: int = 0

    # 一级因子分
    etf_trend: float = 0.0
    etf_accel: float = 0.0
    breadth: float = 0.0
    leader: float = 0.0
    leader_expand: float = 0.0
    money: float = 0.0
    rank_momentum: float = 0.0
    lifecycle_bonus: float = 0.0
    resonance_multiplier: float = 1.0

    # 综合 (IntrinsicScore = 主题自身强度, 不含市场环境)
    intrinsic_score: float = 0.0
    # 市场调整后分数 (TradableScore = Intrinsic × MarketMultiplier × LifecycleAdj × ResonanceAdj)
    tradable_score: float = 0.0
    # final_score 保留为排序别名 (同 tradable_score)
    final_score: float = 0.0

    life_stage: str = "birth"
    transition_direction: str = ""              # 迁移方向: ACCELERATING/PEAKING/...
    transition_strength: float = 0.0             # 迁移强度
    pre_rotate: bool = False                    # 提前轮动信号
    migration_priority: float = 0.0              # 迁移优先级 0~100 (未来接力潜力)
    forward_score: float = 0.0                   # 前瞻评分 (tradable×0.7 + migration×0.3)
    signal: str = "WATCH"
    rotation_prob_5d: float = 0.0
    confidence: float = 0.0
    expected_return: str = ""
    risk: str = ""

    # 市场环境
    market_regime: str = ""
    market_multiplier: float = 1.0
    recommended_exposure: float = 1.0

    # 成分
    top_leaders: List[str] = field(default_factory=list)
    core_stocks: List[str] = field(default_factory=list)
    etf_code: str = ""
    etf_name: str = ""

    # 详细结果
    etf_trend_result: Optional[ETFTrendResult] = None
    etf_accel_result: Optional[ETFAccelResult] = None
    breadth_result: Optional[BreadthResult] = None
    leader_result: Optional[LeaderResult] = None
    leader_expand_result: Optional[LeaderExpandResult] = None
    rank_momentum_result: Optional[RankMomentumResult] = None
    money_flow_result: Optional[MoneyFlowResult] = None
    lifecycle_result: Optional[LifecycleResult] = None
    resonance_result: Optional[ResonanceResult] = None
    transition_result: Optional[TransitionResult] = None

    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MarketInfo:
    """市场环境信息."""
    market_score: float = 0.0
    market_regime: str = "neutral"
    market_regime_cn: str = "Neutral"
    confidence: float = 0.0
    market_multiplier: float = 1.0
    recommended_exposure: float = 1.0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EngineV3Result:
    """V3引擎完整输出."""
    trade_date: str
    themes: List[ThemeV3Score] = field(default_factory=list)
    ranking: List[ThemeV3Score] = field(default_factory=list)
    top_themes: List[ThemeV3Score] = field(default_factory=list)
    generated_at: str = ""
    error: Optional[str] = None
    market_info: Optional[MarketInfo] = None
