"""Market Regime 数据模型."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MarketTrendResult:
    """大盘趋势评分."""
    score: float = 0.0
    index_count: int = 0
    avg_ema20_pos: float = 0.0
    avg_ema60_pos: float = 0.0
    avg_slope_20d: float = 0.0
    avg_new_high_20d: float = 0.0
    avg_drawdown_20d: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MarketBreadthResult:
    """市场宽度评分."""
    score: float = 0.0
    up_ratio: float = 0.0
    new_high_low_ratio: float = 0.0
    consecutive_up_count: int = 0
    advance_decline_ratio: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MarketSentimentResult:
    """市场情绪评分."""
    score: float = 0.0
    limit_up_count: int = 0
    limit_down_count: int = 0
    break_rate: float = 0.0
    consecutive_limit_height: int = 0
    yest_limit_up_perf: float = 0.0
    yest_consecutive_perf: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MarketLiquidityResult:
    """市场成交额评分."""
    score: float = 0.0
    total_amount: float = 0.0
    amount_ma20: float = 0.0
    amount_change_pct: float = 0.0
    etf_amount_change_pct: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MarketRiskPrefResult:
    """风险偏好评分."""
    score: float = 0.0
    growth_defense_ratio: float = 0.0
    growth_etf_perf: float = 0.0
    defense_etf_perf: float = 0.0
    bank_etf_perf: float = 0.0
    tech_etf_perf: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MarketMoneyResult:
    """市场资金评分."""
    score: float = 0.0
    etf_net_inflow: float = 0.0
    margin_balance_change: float = 0.0
    northbound_flow: float = 0.0
    main_net_inflow: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MarketVolatilityResult:
    """市场波动率评分."""
    score: float = 0.0
    avg_atr_ratio: float = 0.0
    avg_amplitude: float = 0.0
    volatility_percentile: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MarketScoreResult:
    """市场综合评分."""
    score: float = 0.0
    trend_score: float = 0.0
    breadth_score: float = 0.0
    sentiment_score: float = 0.0
    liquidity_score: float = 0.0
    risk_pref_score: float = 0.0
    money_score: float = 0.0
    volatility_score: float = 0.0
    # 详细结果
    trend_result: Optional[MarketTrendResult] = None
    breadth_result: Optional[MarketBreadthResult] = None
    sentiment_result: Optional[MarketSentimentResult] = None
    liquidity_result: Optional[MarketLiquidityResult] = None
    risk_pref_result: Optional[MarketRiskPrefResult] = None
    money_result: Optional[MarketMoneyResult] = None
    volatility_result: Optional[MarketVolatilityResult] = None
    details: Dict[str, Any] = field(default_factory=dict)


# ── Regime 定义 ──────────────────────────────────────────

MARKET_REGIMES = ["risk_on", "neutral", "weak", "risk_off", "panic"]

REGIME_CN = {
    "risk_on": "Risk-On",
    "neutral": "Neutral",
    "weak": "Weak",
    "risk_off": "Risk-Off",
    "panic": "Panic",
}


@dataclass
class MarketRegimeResult:
    """市场状态识别结果."""
    regime: str = "neutral"
    regime_cn: str = "Neutral"
    confidence: float = 0.0
    market_score: float = 0.0
    market_multiplier: float = 1.0
    recommended_exposure: float = 1.0
    # 各维度投票
    trend_regime: str = "neutral"
    breadth_regime: str = "neutral"
    sentiment_regime: str = "neutral"
    liquidity_regime: str = "neutral"
    risk_pref_regime: str = "neutral"
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RegimeConfig:
    """Regime 参数配置."""
    risk_on_trend: float = 60.0
    risk_on_breadth: float = 55.0
    risk_on_sentiment: float = 55.0
    risk_on_liquidity: float = 50.0

    weak_trend: float = 35.0
    weak_breadth: float = 35.0
    weak_sentiment: float = 35.0

    risk_off_trend: float = 20.0
    risk_off_breadth: float = 20.0
    risk_off_sentiment: float = 20.0

    panic_trend: float = 10.0
    panic_breadth: float = 10.0

    # 乘数
    multiplier_risk_on: float = 1.10
    multiplier_neutral: float = 1.00
    multiplier_weak: float = 0.85
    multiplier_risk_off: float = 0.70
    multiplier_panic: float = 0.50

    # 推荐仓位
    exposure_risk_on: float = 1.00
    exposure_neutral: float = 0.70
    exposure_weak: float = 0.50
    exposure_risk_off: float = 0.30
    exposure_panic: float = 0.10
