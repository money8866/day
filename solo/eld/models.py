"""
ELD V2 数据模型定义

所有内部数据传输使用 dataclass，确保类型安全。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from .constants import BuyPointState, EarningsBuySignal, InstitutionState, StarRating


# ──────────────────────────────────────────────
# 原始数据模型
# ──────────────────────────────────────────────
@dataclass
class StockBasic:
    """股票基本信息"""
    ts_code: str
    name: str
    industry: str = ""
    area: str = ""
    market: str = ""  # 主板/创业板/科创板/北证


@dataclass
class ForecastData:
    """业绩预告数据"""
    ts_code: str
    end_date: str
    type: str  # 预增/预减/略增/扭亏等
    p_change_min: float = 0.0
    p_change_max: float = 0.0
    announce_date: str = ""
    fiscal_quarter: str = ""
    summary: str = ""  # 业绩预告原文摘要，用于检测非经常性损益关键词


@dataclass
class FinancialData:
    """财务数据"""
    ts_code: str
    end_date: str
    revenue: float = 0.0         # 营业收入
    revenue_yoy: float = 0.0     # 营收同比
    deducted_profit: float = 0.0  # 扣非净利润
    deducted_yoy: float = 0.0    # 扣非同比
    net_profit: float = 0.0      # 净利润
    net_profit_yoy: float = 0.0  # 净利同比
    gross_margin: float = 0.0    # 毛利率
    roe: float = 0.0             # ROE
    roic: float = 0.0            # ROIC
    ocf: float = 0.0             # 经营现金流
    ocf_ratio: float = 0.0       # 经营现金流/净利润
    debt_ratio: float = 0.0      # 资产负债率
    main_biz_ratio: float = 0.0  # 主营业务收入占比


@dataclass
class DailyPriceData:
    """日线价格数据"""
    ts_code: str
    trade_date: str
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    pre_close: float = 0.0
    change: float = 0.0
    pct_change: float = 0.0
    vol: float = 0.0
    amount: float = 0.0


@dataclass
class DailyBasicData:
    """每日指标数据"""
    ts_code: str
    trade_date: str
    turnover_rate: float = 0.0
    turnover_rate_f: float = 0.0
    volume_ratio: float = 0.0
    pe: float = 0.0
    pe_ttm: float = 0.0
    pb: float = 0.0
    total_mv: float = 0.0
    circ_mv: float = 0.0


@dataclass
class MoneyFlowData:
    """资金流向数据"""
    ts_code: str
    trade_date: str
    buy_lg_amount: float = 0.0
    sell_lg_amount: float = 0.0
    buy_md_amount: float = 0.0
    sell_md_amount: float = 0.0
    buy_sm_amount: float = 0.0
    sell_sm_amount: float = 0.0


@dataclass
class CyqData:
    """筹码分布数据"""
    ts_code: str
    trade_date: str
    profit_ratio: float = 0.0       # 获利盘比例
    avg_cost: float = 0.0           # 平均成本
    cost_concentration: float = 0.0 # 成本集中度
    peak_price: float = 0.0         # 成本峰价格
    peak_strength: float = 0.0      # 成本峰强度
    lockup_ratio: float = 0.0       # 锁仓程度


# ──────────────────────────────────────────────
# 评分结果模型
# ──────────────────────────────────────────────
@dataclass
class EventQualityResult:
    """事件质量评分结果"""
    score: float = 0.0
    stars: StarRating = StarRating.ZERO
    deducted_ratio: float = 0.0
    revenue_growth: float = 0.0
    has_non_recurring: bool = False
    non_recurring_items: list[str] = field(default_factory=list)
    logic: list[str] = field(default_factory=list)


@dataclass
class EarningsScoreResult:
    """基本面评分结果"""
    score: float = 0.0
    revenue_growth_score: float = 0.0
    deducted_growth_score: float = 0.0
    net_profit_growth_score: float = 0.0
    gross_margin_score: float = 0.0
    roe_score: float = 0.0
    roic_score: float = 0.0
    ocf_score: float = 0.0
    debt_score: float = 0.0
    main_biz_score: float = 0.0
    consecutive_bonus: float = 0.0
    logic: list[str] = field(default_factory=list)


@dataclass
class InstitutionScoreResult:
    """机构资金评分结果"""
    score: float = 0.0
    short_term_flow: float = 0.0
    mid_term_flow: float = 0.0
    long_term_flow: float = 0.0
    breakout_flow: float = 0.0
    north_flow: float = 0.0
    fund_holding_change: float = 0.0
    consecutive_inflow_days: int = 0
    logic: list[str] = field(default_factory=list)


@dataclass
class ChipScoreResult:
    """筹码评分结果"""
    score: float = 0.0
    profit_ratio: float = 0.0
    avg_cost_diff_pct: float = 0.0
    concentration: float = 0.0
    peak_strength: float = 0.0
    lockup_ratio: float = 0.0
    cost_rise_speed: float = 0.0
    logic: list[str] = field(default_factory=list)


@dataclass
class TrendScoreResult:
    """趋势评分结果"""
    score: float = 0.0
    alpha: float = 0.0
    relative_alpha: float = 0.0
    trend: float = 0.0
    momentum: float = 0.0
    ma_alignment: float = 0.0
    new_high_count: int = 0
    atr_ratio: float = 0.0
    volatility: float = 0.0
    beta: float = 0.0
    relative_strength: float = 0.0
    logic: list[str] = field(default_factory=list)


@dataclass
class IndustryScoreResult:
    """行业评分结果"""
    score: float = 0.0
    industry_rank: int = 999
    theme_score: float = 0.0
    is_top_theme: bool = False
    logic: list[str] = field(default_factory=list)


@dataclass
class FreshnessScoreResult:
    """公告时效评分"""
    score: float = 0.0
    days_since_announce: int = 999
    logic: list[str] = field(default_factory=list)


@dataclass
class ExpectationGapResult:
    """预期差评分"""
    score: float = 0.0
    surprise_type: str = "unknown"  # positive/neutral/negative
    actual_pct: float = 0.0
    expected_pct: float = 0.0
    gap_pct: float = 0.0
    logic: list[str] = field(default_factory=list)


@dataclass
class SimilarityResult:
    """历史相似度结果"""
    score: float = 0.0
    similar_stocks: list[dict[str, Any]] = field(default_factory=list)
    cosine_sim: float = 0.0
    euclidean_dist: float = 0.0
    xgb_probability: float = 0.0
    logic: list[str] = field(default_factory=list)


@dataclass
class BuyPointResult:
    """买点判断结果"""
    state: BuyPointState = BuyPointState.NONE
    rating: StarRating = StarRating.ZERO
    stars_int: int = 0
    ma_alignment: str = ""
    volume_confirmation: bool = False
    atr_position: str = ""
    alpha_direction: str = ""
    chip_confirmation: bool = False
    logic: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────
# ELD V2 新增模块结果模型
# ──────────────────────────────────────────────
@dataclass
class ExpectationGapV2Result:
    """预期差引擎 V2 结果（代理预期模型）"""
    score: float = 0.0
    industry_growth: float = 0.0          # 行业基准增速（%）
    company_growth: float = 0.0           # 公司预告增速（%）
    gap: float = 0.0                      # 超额增长（%）
    acceleration: float = 0.0             # 增长加速度
    profit_acceleration: float = 0.0      # 利润加速度
    revenue_acceleration: float = 0.0     # 营收加速度
    industry_median: float = 0.0          # 行业中位数增速
    industry_mean: float = 0.0            # 行业平均增速
    peer_count: int = 0                   # 可比公司数
    logic: list[str] = field(default_factory=list)


@dataclass
class InstitutionAccumulationResult:
    """机构吸筹检测结果"""
    score: float = 0.0
    state: InstitutionState = InstitutionState.UNKNOWN
    fund_flow_score: float = 0.0          # 资金趋势分
    volume_price_score: float = 0.0       # 量价结构分
    chip_change_score: float = 0.0        # 筹码变化分
    short_term_flow_ratio: float = 0.0    # 近5日净大单流入比
    mid_term_flow_ratio: float = 0.0      # 近10日净大单流入比
    long_term_flow_ratio: float = 0.0     # 近20日净大单流入比
    volume_trend_score: float = 0.0       # 量能趋势得分
    concentration_change: float = 0.0     # 集中度变化
    avg_cost_change: float = 0.0          # 平均成本变化
    profit_ratio_change: float = 0.0      # 获利盘变化
    logic: list[str] = field(default_factory=list)


@dataclass
class EarningsBuyPointResult:
    """业绩回踩买点检测结果"""
    signal: EarningsBuySignal = EarningsBuySignal.NONE
    score: float = 0.0
    stage: str = ""                        # BUY / WATCH / IGNORE
    days_since_announce: int = 0           # 距公告天数
    pullback_from_high_pct: float = 0.0    # 距高点回撤%
    volume_ratio: float = 0.0             # 量比
    close_above_ma20: bool = False         # 是否在MA20上方
    alpha: float = 0.0                    # Alpha值
    institution_state: str = ""            # 当前机构状态
    logic: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────
# 最终评分模型
# ──────────────────────────────────────────────
@dataclass
class MarketScoreResult:
    """市场评分"""
    regime: "MarketRegime" = "unknown"
    multiplier: float = 1.0
    score: float = 50.0
    risk_appetite: float = 50.0
    logic: list[str] = field(default_factory=list)


@dataclass
class FinalScoreResult:
    """最终评分结果"""
    ts_code: str = ""
    name: str = ""
    industry: str = ""
    theme: str = ""
    announce_date: str = ""
    forecast_pct: float = 0.0

    # ELS 各维度（保留原有字段保持兼容）
    event_quality_score: float = 0.0
    earnings_score: float = 0.0
    institution_score: float = 0.0
    chip_score: float = 0.0
    trend_score: float = 0.0
    industry_score: float = 0.0
    freshness_score: float = 0.0
    expectation_gap_score: float = 0.0
    similarity_score: float = 0.0

    # ELD V2 新增维度
    expectation_gap_v2_score: float = 0.0     # 预期差V2
    institution_accumulation_score: float = 0.0  # 机构吸筹评分
    institution_state: str = ""                # 机构状态
    earnings_buy_signal: str = ""              # 业绩回踩买点信号
    earnings_buy_score: float = 0.0            # 业绩回踩买点评分

    # 综合
    els: float = 0.0
    els_v2: float = 0.0    # ELD V2 评分
    market_multiplier: float = 1.0
    final_score: float = 0.0
    final_score_v2: float = 0.0  # ELD V2 最终分
    rank: int = 0

    # 详细结果（用于报告）
    event_detail: Optional[EventQualityResult] = None
    earnings_detail: Optional[EarningsScoreResult] = None
    institution_detail: Optional[InstitutionScoreResult] = None
    chip_detail: Optional[ChipScoreResult] = None
    trend_detail: Optional[TrendScoreResult] = None
    industry_detail: Optional[IndustryScoreResult] = None
    freshness_detail: Optional[FreshnessScoreResult] = None
    expectation_gap_detail: Optional[ExpectationGapResult] = None
    similarity_detail: Optional[SimilarityResult] = None

    # ELD V2 新增详细结果
    expectation_gap_v2_detail: Optional[ExpectationGapV2Result] = None
    institution_accumulation_detail: Optional[InstitutionAccumulationResult] = None
    earnings_buy_point_detail: Optional[EarningsBuyPointResult] = None

    buy_point_detail: Optional[BuyPointResult] = None

    recommendation: str = "观望"
    recommendation_v2: str = "观望"  # V2 建议

    def to_dict(self) -> dict[str, Any]:
        """转为扁平字典"""
        d = {
            "ts_code": self.ts_code,
            "name": self.name,
            "industry": self.industry,
            "theme": self.theme,
            "announce_date": self.announce_date,
            "forecast_pct": self.forecast_pct,
            "els": round(self.els, 2),
            "els_v2": round(self.els_v2, 2),
            "final_score": round(self.final_score, 2),
            "final_score_v2": round(self.final_score_v2, 2),
            "rank": self.rank,
            "event_quality": round(self.event_quality_score, 1),
            "earnings": round(self.earnings_score, 1),
            "institution": round(self.institution_score, 1),
            "chip": round(self.chip_score, 1),
            "trend": round(self.trend_score, 1),
            "industry_score": round(self.industry_score, 1),
            "freshness": round(self.freshness_score, 1),
            "expectation_gap": round(self.expectation_gap_score, 1),
            "similarity": round(self.similarity_score, 1),
            # V2 新增字段
            "expectation_gap_v2": round(self.expectation_gap_v2_score, 1),
            "institution_accumulation": round(self.institution_accumulation_score, 1),
            "institution_state": self.institution_state,
            "earnings_buy_signal": self.earnings_buy_signal,
            "earnings_buy_score": round(self.earnings_buy_score, 1),
            "buy_point": self.buy_point_detail.state.value if self.buy_point_detail else "",
            "recommendation": self.recommendation,
            "recommendation_v2": self.recommendation_v2,
        }
        return d


# ──────────────────────────────────────────────
# 报告模型
# ──────────────────────────────────────────────
@dataclass
class EldReport:
    """系统报告"""
    run_date: str = ""
    total_stocks: int = 0
    filtered_stocks: int = 0
    market_regime: str = ""
    results: list[FinalScoreResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_date": self.run_date,
            "total_stocks": self.total_stocks,
            "filtered_stocks": self.filtered_stocks,
            "market_regime": self.market_regime,
            "results": [r.to_dict() for r in self.results],
        }
