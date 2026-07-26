"""TERE V1 Pydantic 数据模型 — 定义全部评分数据结构."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional


# ════════════════════════════════════════════════════════════
#  基础类型
# ════════════════════════════════════════════════════════════

@dataclass
class FactorResult:
    """单个因子计算结果."""
    factor_name: str
    version: str
    score: float          # 0~100
    weight: float         # 当前权重
    contribution: float   # score * weight / sum(weight)
    details: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


# ════════════════════════════════════════════════════════════
#  各层级评分结果
# ════════════════════════════════════════════════════════════

@dataclass
class ETFStrengthResult:
    """ETF 强度评分结果."""
    theme_code: str
    trade_date: str
    main_etf: str
    backup_etf: Optional[str] = None
    trend_score: float = 0.0
    momentum_score: float = 0.0
    alpha_score: float = 0.0
    volume_score: float = 0.0
    money_flow_score: float = 0.0
    volatility_score: float = 0.0
    relative_strength: float = 0.0
    ma_trend: float = 0.0
    slope: float = 0.0
    atr_score: float = 0.0
    breakout_score: float = 0.0
    etf_strength: float = 0.0  # 总分 0~100
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BreadthResult:
    """主题扩散度评分."""
    theme_code: str
    trade_date: str
    total_stocks: int = 0
    up_ratio: float = 0.0
    limit_up_ratio: float = 0.0
    new_high_20d_ratio: float = 0.0
    above_ma20_ratio: float = 0.0
    above_ma60_ratio: float = 0.0
    above_ma120_ratio: float = 0.0
    amount_diffusion: float = 0.0
    return_median: float = 0.0
    avg_alpha: float = 0.0
    avg_relative_alpha: float = 0.0
    breadth_score: float = 0.0  # 总分 0~100
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LeaderResult:
    """龙头/核心识别评分."""
    theme_code: str
    trade_date: str
    leader_count: int = 0
    core_count: int = 0
    follower_count: int = 0
    leader_trend: float = 0.0
    leader_alpha: float = 0.0
    relative_strength: float = 0.0
    volume_score: float = 0.0
    money_flow_score: float = 0.0
    institution_score: float = 0.0
    macd_score: float = 0.0
    ma_trend_score: float = 0.0
    leader_strength: float = 0.0  # 总分 0~100
    leaders: List[Dict[str, Any]] = field(default_factory=list)
    cores: List[Dict[str, Any]] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PurityResult:
    """主题纯度评分."""
    theme_code: str
    trade_date: str
    theme_purity: float = 0.0      # 平均纯度
    weighted_breadth: float = 0.0  # 纯度加权扩散
    weighted_alpha: float = 0.0    # 纯度加权 alpha
    purity_score: float = 0.0      # 总分 0~100
    stock_purities: List[Dict[str, Any]] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ResonanceResult:
    """ETF-Theme 共振评分."""
    theme_code: str
    trade_date: str
    etf_strength: float = 0.0
    theme_breadth: float = 0.0
    leader_score: float = 0.0
    consistency_score: float = 0.0
    variance_penalty: float = 0.0
    std: float = 0.0
    correlation: float = 0.0
    resonance_score: float = 0.0  # 总分 0~100
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FlowResult:
    """资金流评分."""
    theme_code: str
    trade_date: str
    etf_net_flow: float = 0.0
    theme_total_amount: float = 0.0
    leader_amount: float = 0.0
    amount_change_pct: float = 0.0
    volume_change_pct: float = 0.0
    flow_diffusion: float = 0.0
    flow_score: float = 0.0  # 总分 0~100
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StageResult:
    """生命周期阶段判定."""
    theme_code: str
    trade_date: str
    current_stage: str = "birth"
    stage_confidence: float = 0.0
    days_in_stage: int = 0
    stage_progress: float = 0.0  # 0~1, 在阶段内的进度
    next_stage: Optional[str] = None
    indicators: Dict[str, Any] = field(default_factory=dict)
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SignalResult:
    """交易信号."""
    theme_code: str
    trade_date: str
    signal: str = "WATCH"       # STRONG_BUY / BUY / WATCH / REDUCE / EXIT
    signal_strength: float = 0.0  # 0~100
    reasons: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RotationResult:
    """主线轮动概率."""
    theme_code: str
    trade_date: str
    prob_3d: float = 0.0   # 未来3日继续为主线的概率
    prob_5d: float = 0.0   # 未来5日
    prob_10d: float = 0.0  # 未来10日
    etf_momentum: float = 0.0
    leader_momentum: float = 0.0
    breadth_trend: float = 0.0
    resonance_trend: float = 0.0
    rotation_score: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


# ════════════════════════════════════════════════════════════
#  综合输出
# ════════════════════════════════════════════════════════════

@dataclass
class ExplainItem:
    """可解释 AI 条目."""
    reason: str
    score: float
    weight: float = 1.0


@dataclass
class ThemeDailyScore:
    """每日主线主题排行榜条目."""
    rank: int = 0
    theme_code: str = ""
    theme_name: str = ""
    total_score: float = 0.0       # 加权总分 0~100
    etf_strength: float = 0.0
    breadth_score: float = 0.0
    leader_strength: float = 0.0
    purity_score: float = 0.0
    resonance_score: float = 0.0
    flow_score: float = 0.0
    stage: str = ""
    rotation_prob: float = 0.0
    signal: str = "WATCH"
    top_leaders: List[str] = field(default_factory=list)
    top_stocks: List[str] = field(default_factory=list)
    main_etf: str = ""
    backup_etf: Optional[str] = None
    explanations: List[ExplainItem] = field(default_factory=list)
    summary: str = ""
    trade_date: str = ""
    created_at: str = ""


@dataclass
class EngineResult:
    """引擎完整输出."""
    trade_date: str
    themes: List[ThemeDailyScore] = field(default_factory=list)
    ranking: List[ThemeDailyScore] = field(default_factory=list)
    top_themes: List[ThemeDailyScore] = field(default_factory=list)
    generated_at: str = ""
    error: Optional[str] = None
