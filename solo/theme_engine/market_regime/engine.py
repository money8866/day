"""Market Regime Engine V1 — 市场状态识别系统.

作为整个系统最顶层：
Market → Theme → ETF → Leader → Signal

自动判断市场状态：
Risk-On / Neutral / Weak / Risk-Off / Panic

提供 MarketMultiplier 调整所有主题评分。
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from .config import (
    load_config,
    get_layer_weights,
    get_regime_threshold,
    get_multiplier,
    get_exposure,
)
from .data import MarketDataFetcher
from .factors.trend import calc_market_trend
from .factors.breadth import calc_market_breadth
from .factors.sentiment import calc_market_sentiment
from .factors.liquidity import calc_market_liquidity
from .factors.risk_pref import calc_market_risk_pref
from .factors.money import calc_market_money
from .factors.volatility import calc_market_volatility
from .models import (
    MARKET_REGIMES,
    REGIME_CN,
    MarketScoreResult,
    MarketRegimeResult,
    MarketTrendResult,
    MarketBreadthResult,
    MarketSentimentResult,
    MarketLiquidityResult,
    MarketRiskPrefResult,
    MarketMoneyResult,
    MarketVolatilityResult,
)

logger = logging.getLogger(__name__)


class MarketRegimeEngine:
    """市场状态识别引擎.

    每天运行一次，输出当天市场状态，供主题评分系统使用。
    """

    def __init__(self) -> None:
        self.fetcher = MarketDataFetcher()
        self._weights: Dict[str, float] = {}

    async def analyze(
        self,
        trade_date: str,
        skip_factors: Optional[List[str]] = None,
    ) -> MarketRegimeResult:
        """执行完整市场状态分析.

        Args:
            trade_date: 交易日 YYYYMMDD
            skip_factors: 跳过的因子列表

        Returns:
            MarketRegimeResult 包含市场状态、置信度、乘数
        """
        skip = skip_factors or []

        # ── 1. 计算7个因子分 ──
        trend_result = await calc_market_trend(self.fetcher, trade_date) if "trend" not in skip else MarketTrendResult()
        breadth_result = await calc_market_breadth(self.fetcher, trade_date) if "breadth" not in skip else MarketBreadthResult()
        sentiment_result = await calc_market_sentiment(self.fetcher, trade_date) if "sentiment" not in skip else MarketSentimentResult()
        liquidity_result = await calc_market_liquidity(self.fetcher, trade_date) if "liquidity" not in skip else MarketLiquidityResult()
        risk_pref_result = await calc_market_risk_pref(self.fetcher, trade_date) if "risk_pref" not in skip else MarketRiskPrefResult()
        money_result = await calc_market_money(self.fetcher, trade_date) if "money" not in skip else MarketMoneyResult()
        volatility_result = await calc_market_volatility(self.fetcher, trade_date) if "volatility" not in skip else MarketVolatilityResult()

        # ── 2. 综合 MarketScore ──
        self._load_weights()
        factor_scores = {
            "trend": trend_result.score,
            "breadth": breadth_result.score,
            "sentiment": sentiment_result.score,
            "liquidity": liquidity_result.score,
            "risk_pref": risk_pref_result.score,
            "money": money_result.score,
            "volatility": volatility_result.score,
        }

        total_weight = sum(self._weights.values())
        market_score = 0.0
        if total_weight > 0:
            for key, weight in self._weights.items():
                market_score += factor_scores.get(key, 0.0) * weight
            market_score /= total_weight

        # ── 3. 各维度独立Regime投票 ──
        trend_reg = self._dimension_regime(trend_result.score, "trend")
        breadth_reg = self._dimension_regime(breadth_result.score, "breadth")
        sentiment_reg = self._dimension_regime(sentiment_result.score, "sentiment")
        liquidity_reg = self._dimension_regime(liquidity_result.score, "liquidity")
        risk_pref_reg = self._dimension_regime(risk_pref_result.score, "risk_pref")

        # ── 4. 综合Regime判定 ──
        regime, confidence = self._determine_regime(
            trend_score=trend_result.score,
            breadth_score=breadth_result.score,
            sentiment_score=sentiment_result.score,
            liquidity_score=liquidity_result.score,
            market_score=market_score,
            trend_regime=trend_reg,
            breadth_regime=breadth_reg,
            sentiment_regime=sentiment_reg,
        )

        # ── 5. 乘数和仓位 (Bayesian Shrinkage: 低置信度时收缩到1.0) ──
        multiplier = get_multiplier(regime)
        # confidence ∈ [0.3, 0.99], confidence 越低, multiplier 越接近 1.0
        multiplier = multiplier * confidence + 1.0 * (1.0 - confidence)
        exposure = get_exposure(regime)

        result = MarketRegimeResult(
            regime=regime,
            regime_cn=REGIME_CN.get(regime, "Neutral"),
            confidence=round(confidence, 2),
            market_score=round(market_score, 2),
            market_multiplier=multiplier,
            recommended_exposure=exposure,
            trend_regime=trend_reg,
            breadth_regime=breadth_reg,
            sentiment_regime=sentiment_reg,
            liquidity_regime=liquidity_reg,
            risk_pref_regime=risk_pref_reg,
            details={
                "factor_scores": {k: round(v, 2) for k, v in factor_scores.items()},
                "vote_results": {
                    "trend": trend_reg,
                    "breadth": breadth_reg,
                    "sentiment": sentiment_reg,
                    "liquidity": liquidity_reg,
                    "risk_pref": risk_pref_reg,
                },
            },
        )

        logger.info(
            "Market Regime: %s | Score=%.1f | Confidence=%.0f%% | Multiplier=%.2f | Exposure=%.0f%%",
            result.regime_cn, market_score, confidence * 100, multiplier, exposure * 100,
        )

        return result

    def _dimension_regime(self, score: float, dimension: str) -> str:
        """单个维度的Regime判断."""
        cfg = load_config()
        thresholds = cfg.get("regime_thresholds", {})

        ro_t = thresholds.get(f"risk_on_{dimension}", 60)
        wk_t = thresholds.get(f"weak_{dimension}", 35)
        rof_t = thresholds.get(f"risk_off_{dimension}", 20)
        p_t = thresholds.get(f"panic_{dimension}", 10)

        if score >= ro_t:
            return "risk_on"
        elif score >= wk_t:
            return "neutral"
        elif score >= rof_t:
            return "weak"
        elif score >= p_t:
            return "risk_off"
        else:
            return "panic"

    def _determine_regime(
        self,
        trend_score: float,
        breadth_score: float,
        sentiment_score: float,
        liquidity_score: float,
        market_score: float,
        trend_regime: str,
        breadth_regime: str,
        sentiment_regime: str,
    ) -> Tuple[str, float]:
        """多维度综合判定市场状态.

        投票机制:
        - trend/breadth/sentiment 各一票
        - market_score 作为整体参考
        - 加权投票决定最终Regime
        """
        # 各维度投票权重
        vote_weights = {"risk_on": 4, "neutral": 3, "weak": 2, "risk_off": 1, "panic": 0}

        votes = [
            (trend_regime, 0.40),
            (breadth_regime, 0.25),
            (sentiment_regime, 0.20),
            # 市场分数映射到等效Regime
            (self._score_to_regime(market_score), 0.15),
        ]

        weighted_sum = 0.0
        total_vote_weight = sum(w for _, w in votes)
        for reg, w in votes:
            weighted_sum += vote_weights.get(reg, 2) * w

        avg_vote = weighted_sum / total_vote_weight if total_vote_weight > 0 else 2

        # 映射回Regime
        if avg_vote >= 3.5:
            regime = "risk_on"
        elif avg_vote >= 2.5:
            regime = "neutral"
        elif avg_vote >= 1.5:
            regime = "weak"
        elif avg_vote >= 0.5:
            regime = "risk_off"
        else:
            regime = "panic"

        # 置信度: 各维度一致性越高越自信
        regime_values = [vote_weights.get(r, 2) for r in [trend_regime, breadth_regime, sentiment_regime]]
        consistency = 1.0 - (max(regime_values) - min(regime_values)) / 4.0
        score_extreme = abs(market_score - 50) / 50.0
        confidence = consistency * 0.6 + score_extreme * 0.4
        confidence = max(0.3, min(0.99, confidence))

        # 一些硬性规则
        if regime == "risk_on" and (trend_score < 40 or breadth_score < 35):
            regime = "neutral"
            confidence *= 0.8
        elif regime == "panic" and trend_score > 25:
            regime = "risk_off"
            confidence *= 0.9

        return regime, confidence

    @staticmethod
    def _score_to_regime(score: float) -> str:
        """MarketScore 映射到 Regime."""
        if score >= 65:
            return "risk_on"
        elif score >= 40:
            return "neutral"
        elif score >= 25:
            return "weak"
        elif score >= 12:
            return "risk_off"
        else:
            return "panic"

    def _load_weights(self) -> None:
        """加载因子权重."""
        self._weights = get_layer_weights()

    async def cleanup(self) -> None:
        """清理资源."""
        await self.fetcher.clear_cache()
