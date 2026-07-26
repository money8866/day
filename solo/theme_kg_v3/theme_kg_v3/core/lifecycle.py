"""主题生命周期判定算法 - Birth / Growth / Main Trend / Distribution / Death."""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from theme_kg_v3.schema.dataclasses import LifecycleResult
from theme_kg_v3.config.settings import LIFECYCLE_THRESHOLDS

logger = logging.getLogger(__name__)

# ── 阶段常量 ────────────────────────────────────────────────
STAGE_BIRTH = "birth"
STAGE_GROWTH = "growth"
STAGE_MAIN_TREND = "main_trend"
STAGE_DISTRIBUTION = "distribution"
STAGE_DEATH = "death"

STAGE_ORDER = [STAGE_BIRTH, STAGE_GROWTH, STAGE_MAIN_TREND, STAGE_DISTRIBUTION, STAGE_DEATH]


class LifecycleAnalyzer:
    """主题生命周期分析器.

    根据主题的历史日频快照数据（动量、成交量、龙头数、市值、情绪等），
    通过多因子规则判定当前所处生命周期阶段，并预测下一阶段走向.
    """

    def __init__(self) -> None:
        """初始化分析器，加载生命周期阈值配置."""
        self.thresholds = LIFECYCLE_THRESHOLDS
        logger.info(
            "LifecycleAnalyzer initialized with thresholds: birth_min_concept=%s, "
            "growth_min_momentum=%s, main_trend_min_leaders=%s",
            self.thresholds.get("birth", {}).get("min_concept_count", 3),
            self.thresholds.get("growth", {}).get("min_momentum_20d", 0.05),
            self.thresholds.get("main_trend", {}).get("min_leader_count", 3),
        )

    # ──────────────────────────────────────────────
    # 主入口
    # ──────────────────────────────────────────────

    def analyze(
        self,
        theme_code: str,
        theme_name: str,
        history: List[Dict[str, Any]],
    ) -> LifecycleResult:
        """对给定主题执行生命周期判定.

        Args:
            theme_code: 主题代码.
            theme_name: 主题中文名称.
            history: 日频快照列表，每个字典包含:
                trade_date, momentum_5d, momentum_20d, volume_ratio,
                leader_count, total_market_cap_billion, avg_return_5d,
                avg_return_20d, turnover_rate, sentiment_score.

        Returns:
            LifecycleResult 包含当前阶段、置信度、指标详情、下一阶段预测及持续天数.
        """
        if not history:
            return LifecycleResult(
                theme_code=theme_code,
                theme_name=theme_name,
                current_stage=STAGE_BIRTH,
                stage_confidence=0.0,
                indicators={"reason": "无历史数据，默认萌芽期"},
                next_stage_prediction=None,
                days_in_stage=0,
            )

        # 计算全部指标
        indicators = self._compute_indicators(history)

        # 判定阶段
        current_stage = self._determine_stage(history, indicators)

        # 计算置信度
        confidence = self._compute_stage_confidence(current_stage, indicators)

        # 预测下一阶段
        next_stage = self._predict_next_stage(current_stage, indicators)

        # 计算持续天数
        days_in_stage = self._days_in_current_stage(history, current_stage)

        return LifecycleResult(
            theme_code=theme_code,
            theme_name=theme_name,
            current_stage=current_stage,
            stage_confidence=round(confidence, 2),
            indicators=indicators,
            next_stage_prediction=next_stage,
            days_in_stage=days_in_stage,
        )

    # ──────────────────────────────────────────────
    # 指标计算
    # ──────────────────────────────────────────────

    def _compute_indicators(self, history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """从历史数据中计算所有阶段判定所需指标.

        Args:
            history: 日频快照列表（按日期升序排列）.

        Returns:
            包含全部指标的字典.
        """
        n = len(history)
        latest = history[-1] if history else {}
        indicators: Dict[str, Any] = {}

        # ── 基础数据天数 ──
        indicators["data_days"] = n

        # ── 动量趋势 ──
        indicators["momentum_5d"] = latest.get("momentum_5d", 0.0) or 0.0
        indicators["momentum_20d"] = latest.get("momentum_20d", 0.0) or 0.0

        # 60d 动量：用最新数据估算或用已有字段
        indicators["momentum_60d"] = latest.get("momentum_60d", indicators["momentum_20d"]) or 0.0

        # 动量变化（5d - 20d，负值表示动量衰减）
        indicators["momentum_divergence"] = indicators["momentum_5d"] - indicators["momentum_20d"]

        # 动量连续正值天数
        indicators["momentum_positive_days_20d"] = self._count_consecutive_positive(
            history, "momentum_20d", 20,
        )

        # ── 成交量相关 ──
        indicators["volume_ratio"] = latest.get("volume_ratio", 0.0) or 0.0

        # 成交量趋势（近5日均值 vs 近20日均值）
        volume_ratios_5d = [
            h.get("volume_ratio", 0.0) or 0.0 for h in history[-5:]
        ] if n >= 5 else [h.get("volume_ratio", 0.0) or 0.0 for h in history]
        volume_ratios_20d = [
            h.get("volume_ratio", 0.0) or 0.0 for h in history[-20:]
        ] if n >= 20 else [h.get("volume_ratio", 0.0) or 0.0 for h in history]

        indicators["volume_ratio_avg_5d"] = (
            sum(volume_ratios_5d) / len(volume_ratios_5d) if volume_ratios_5d else 0.0
        )
        indicators["volume_ratio_avg_20d"] = (
            sum(volume_ratios_20d) / len(volume_ratios_20d) if volume_ratios_20d else 0.0
        )

        # 成交量扩张倍数（近5d / 近20d）
        indicators["volume_expansion"] = (
            indicators["volume_ratio_avg_5d"] / indicators["volume_ratio_avg_20d"]
            if indicators["volume_ratio_avg_20d"] > 0 else 1.0
        )

        # ── 龙头相关 ──
        indicators["leader_count"] = latest.get("leader_count", 0) or 0
        leader_counts = [h.get("leader_count", 0) or 0 for h in history]
        indicators["leader_count_max"] = max(leader_counts) if leader_counts else 0
        indicators["leader_count_change_5d"] = (
            indicators["leader_count"] - (leader_counts[-5] if n >= 5 else leader_counts[0])
        )

        # ── 市值相关 ──
        indicators["total_market_cap_billion"] = latest.get("total_market_cap_billion", 0.0) or 0.0
        market_caps = [h.get("total_market_cap_billion", 0.0) or 0.0 for h in history]
        indicators["market_cap_peak"] = max(market_caps) if market_caps else 0.0

        # 从峰值回撤
        indicators["drawdown_from_peak"] = (
            (indicators["total_market_cap_billion"] - indicators["market_cap_peak"])
            / indicators["market_cap_peak"]
            if indicators["market_cap_peak"] > 0 else 0.0
        )

        # 市值变化率（近5d）
        indicators["market_cap_change_5d"] = (
            (market_caps[-1] - market_caps[-5]) / market_caps[-5]
            if n >= 5 and market_caps[-5] > 0 else 0.0
        )

        # ── 收益率相关 ──
        indicators["avg_return_5d"] = latest.get("avg_return_5d", 0.0) or 0.0
        indicators["avg_return_20d"] = latest.get("avg_return_20d", 0.0) or 0.0

        # ── 换手率相关 ──
        indicators["turnover_rate"] = latest.get("turnover_rate", 0.0) or 0.0
        turnover_rates = [h.get("turnover_rate", 0.0) or 0.0 for h in history]
        indicators["turnover_peak"] = max(turnover_rates) if turnover_rates else 0.0

        # 量价背离：高换手但价格不涨
        indicators["volume_price_divergence"] = (
            indicators["turnover_rate"] > 0 and indicators["momentum_5d"] <= 0
            and indicators["volume_ratio"] > 1.2
        )

        # ── 情绪相关 ──
        indicators["sentiment_score"] = latest.get("sentiment_score", 50.0) or 50.0
        sentiment_scores = [h.get("sentiment_score", 50.0) or 50.0 for h in history]
        indicators["sentiment_peak"] = max(sentiment_scores) if sentiment_scores else 50.0
        indicators["sentiment_change_5d"] = (
            sentiment_scores[-1] - sentiment_scores[-5]
            if n >= 5 else 0.0
        )

        # ── 宽度（活跃股票比例估算） ──
        # 使用 leader_count / 估算总股票数作为宽度的代理指标
        # 如果 leader_count 不可用，用 volume_ratio > 0.8 的占比
        indicators["active_ratio"] = self._estimate_active_ratio(history)

        # ── 集中度：前N只股票的市值集中度（简化版） ──
        # 用 leader_count / max(leader_count) 近似
        indicators["concentration_ratio"] = (
            indicators["leader_count"] / indicators["leader_count_max"]
            if indicators["leader_count_max"] > 0 else 0.0
        )

        # ── 概念数量（仅当首次数据可用时） ──
        indicators["concept_count"] = latest.get("concept_count", 0) or 0

        # ── 新增催化/新闻 ──
        indicators["has_new_catalyst"] = latest.get("has_new_catalyst", False)
        indicators["days_since_last_catalyst"] = self._days_since_last_event(
            history, "has_new_catalyst",
        )

        # ── 交易量 vs 峰值 ──
        indicators["volume_vs_peak"] = (
            indicators["volume_ratio_avg_20d"] / max(
                [h.get("volume_ratio", 0.0) or 0.0 for h in history[-60:]] or [1.0],
            )
            if n > 0 else 0.0
        )

        # ── 创新高 leader 连续涨停天数均值 ──
        indicators["avg_consecutive_limit_up"] = latest.get("avg_consecutive_limit_up", 0) or 0

        return indicators

    @staticmethod
    def _count_consecutive_positive(
        history: List[Dict[str, Any]],
        field: str,
        max_lookback: int,
    ) -> int:
        """统计最近 N 个交易日中指标连续为正的天数.

        Args:
            history: 历史数据列表.
            field: 指标字段名.
            max_lookback: 最大回看天数.

        Returns:
            连续为正的天数.
        """
        relevant = history[-max_lookback:]
        count = 0
        for h in reversed(relevant):
            val = h.get(field, 0.0) or 0.0
            if val > 0:
                count += 1
            else:
                break
        return count

    @staticmethod
    def _estimate_active_ratio(history: List[Dict[str, Any]]) -> float:
        """估算主题中活跃股票占比.

        基于 volume_ratio 平均值作为活跃程度的代理指标.
        如果 volume_ratio > 0.8 的天数占比越高，活跃度越高.

        Args:
            history: 历史数据列表.

        Returns:
            0-1 之间的活跃比例.
        """
        if not history:
            return 0.0

        # 用最新 N 天的 volume_ratio 平均值 / 2 来估算
        # volume_ratio=1.0 表示等于20日均量，>0.8 视为活跃
        recent = history[-20:] if len(history) >= 20 else history
        active_count = sum(1 for h in recent if (h.get("volume_ratio", 0.0) or 0.0) >= 0.8)
        return active_count / len(recent) if recent else 0.0

    @staticmethod
    def _days_since_last_event(
        history: List[Dict[str, Any]],
        field: str,
    ) -> int:
        """计算自最近一次事件以来的天数.

        Args:
            history: 历史数据列表.
            field: 布尔型事件字段.

        Returns:
            距最近事件的天数，若无则返回一个很大值.
        """
        for i, h in enumerate(reversed(history)):
            if h.get(field, False):
                return i
        return 9999

    # ──────────────────────────────────────────────
    # 阶段判定
    # ──────────────────────────────────────────────

    def _determine_stage(
        self,
        history: List[Dict[str, Any]],
        indicators: Dict[str, Any],
    ) -> str:
        """多因子规则判定当前生命周期阶段.

        按优先级：Death > Distribution > Main Trend > Growth > Birth.

        Args:
            history: 历史数据列表.
            indicators: 计算好的指标字典.

        Returns:
            当前阶段名称常量.
        """
        n = len(history)

        # ── 先判断退潮期 ──
        if self._check_death(history, indicators):
            return STAGE_DEATH

        # ── 判断出货/分歧期 ──
        if self._check_distribution(history, indicators):
            return STAGE_DISTRIBUTION

        # ── 判断主升期 ──
        if self._check_main_trend(history, indicators):
            return STAGE_MAIN_TREND

        # ── 判断成长期 ──
        if self._check_growth(history, indicators):
            return STAGE_GROWTH

        return STAGE_BIRTH

    def _check_birth(
        self,
        history: List[Dict[str, Any]],
        indicators: Dict[str, Any],
    ) -> bool:
        """萌芽期检查.

        条件:
            - 形成天数 < 30
            - 概念数 >= 3
            - volume_ratio > 0.5
            - momentum_20d < 5%
        """
        th = self.thresholds.get("birth", {})
        min_concept = th.get("min_concept_count", 3)
        min_volume = th.get("min_etf_volume_ratio", 0.5)

        return (
            indicators["data_days"] < 30
            and indicators.get("concept_count", 0) >= min_concept
            and indicators["volume_ratio"] > min_volume
            and indicators["momentum_20d"] < 0.05
        )

    def _check_growth(
        self,
        history: List[Dict[str, Any]],
        indicators: Dict[str, Any],
    ) -> bool:
        """成长期检查.

        条件:
            - momentum_20d >= 5%
            - volume_ratio >= 1.2
            - leader_count >= 1
            - sentiment >= 50（中性偏积极）
        """
        th = self.thresholds.get("growth", {})
        min_momentum = th.get("min_momentum_20d", 0.05)
        min_volume = th.get("min_volume_ratio", 1.2)

        return (
            indicators["momentum_20d"] >= min_momentum
            and indicators["volume_ratio"] >= min_volume
            and indicators["leader_count"] >= 1
            and indicators["sentiment_score"] >= 50.0
        )

    def _check_main_trend(
        self,
        history: List[Dict[str, Any]],
        indicators: Dict[str, Any],
    ) -> bool:
        """主升期检查.

        条件:
            - momentum_20d >= 15% 或 momentum_60d >= 30%
            - volume_ratio 持续 >= 1.3
            - leader_count >= 3
            - sentiment > 70
            - 市值正增长
        """
        th = self.thresholds.get("main_trend", {})
        min_trend = th.get("min_trend_strength", 0.70)
        min_leaders = th.get("min_leader_count", 3)

        # 强动量条件
        has_strong_momentum = (
            indicators["momentum_20d"] >= 0.15
            or indicators["momentum_60d"] >= 0.30
        )

        # 成交量条件（近5日均值 >= 1.3）
        has_volume_support = indicators["volume_ratio_avg_5d"] >= 1.3

        # 龙头条件
        has_leaders = indicators["leader_count"] >= min_leaders

        # 情绪条件
        has_sentiment = indicators["sentiment_score"] > 70.0

        # 市值增长
        has_mcap_growth = indicators["market_cap_change_5d"] > 0.0

        # 综合判定：至少满足 4/5 条件
        conditions_met = sum([
            has_strong_momentum,
            has_volume_support,
            has_leaders,
            has_sentiment,
            has_mcap_growth,
        ])

        # 趋势强度评分（用于阈值比较）
        trend_strength = (
            indicators["momentum_20d"] * 2.0
            + (indicators["volume_ratio"] - 1.0) * 0.5
            + (indicators["sentiment_score"] / 100.0) * 0.3
        )
        indicators["_trend_strength"] = trend_strength

        return conditions_met >= 4 or trend_strength >= min_trend

    def _check_distribution(
        self,
        history: List[Dict[str, Any]],
        indicators: Dict[str, Any],
    ) -> bool:
        """出货/分歧期检查.

        条件:
            - 从峰值回撤 >= 15%
            - 量价背离（高量不涨）
            - momentum_5d < momentum_20d（动量衰减）
            - 龙头数下降
            - 情绪从高位回落
        """
        th = self.thresholds.get("distribution", {})
        max_drawdown = th.get("max_drawdown_from_peak", -0.15)

        # 回撤条件
        has_drawdown = indicators["drawdown_from_peak"] <= max_drawdown

        # 量价背离
        has_divergence = indicators["volume_price_divergence"]

        # 动量衰减
        has_momentum_decay = indicators["momentum_divergence"] < -0.02

        # 龙头减少
        has_leader_decline = indicators["leader_count_change_5d"] < 0

        # 情绪回落
        has_sentiment_decline = (
            indicators["sentiment_change_5d"] < 0
            and indicators["sentiment_score"] > 50.0  # 仍偏高但回落中
        )

        # 综合：至少满足 3/5
        conditions_met = sum([
            has_drawdown,
            has_divergence,
            has_momentum_decay,
            has_leader_decline,
            has_sentiment_decline,
        ])

        return conditions_met >= 3

    def _check_death(
        self,
        history: List[Dict[str, Any]],
        indicators: Dict[str, Any],
    ) -> bool:
        """退潮期检查.

        条件:
            - 活跃股比例 < 5%
            - 成交量 < 峰值的30%
            - 无新龙头涌现（60天以上）
            - 无新催化/新闻（30天以上）
        """
        th = self.thresholds.get("death", {})
        max_active_ratio = th.get("max_active_days_ratio", 0.05)
        max_volume_ratio = th.get("max_trading_volume", 0.3)

        # 活跃比例
        low_activity = indicators["active_ratio"] <= max_active_ratio

        # 成交量萎缩
        low_volume = indicators["volume_vs_peak"] <= max_volume_ratio

        # 无新龙头
        no_new_leaders = indicators.get("avg_consecutive_limit_up", 0) == 0

        # 无催化/新闻
        no_catalyst = indicators["days_since_last_catalyst"] >= 30

        # 综合：满足 3/4
        conditions_met = sum([low_activity, low_volume, no_new_leaders, no_catalyst])
        return conditions_met >= 3

    # ──────────────────────────────────────────────
    # 置信度、预测、天数
    # ──────────────────────────────────────────────

    def _compute_stage_confidence(
        self,
        stage: str,
        indicators: Dict[str, Any],
    ) -> float:
        """计算当前阶段判定的置信度分数.

        基于阶段特征的满足程度，分数范围 0-100.

        Args:
            stage: 判定的阶段.
            indicators: 指标字典.

        Returns:
            置信度 0-100.
        """
        if stage == STAGE_BIRTH:
            # 萌芽期置信度：看形成天数（越短越确定）+ 概念数
            days_factor = max(0, 1.0 - indicators["data_days"] / 30.0)
            concept_factor = min(1.0, indicators.get("concept_count", 0) / 5.0)
            volume_factor = min(1.0, indicators["volume_ratio"] / 1.0)
            score = (days_factor * 40 + concept_factor * 30 + volume_factor * 30)

        elif stage == STAGE_GROWTH:
            # 成长期置信度：动量 + 量 + 龙头
            momentum_factor = min(1.0, indicators["momentum_20d"] / 0.10)
            volume_factor = min(1.0, (indicators["volume_ratio"] - 1.0) / 0.5)
            leader_factor = min(1.0, indicators["leader_count"] / 3.0)
            score = (momentum_factor * 35 + volume_factor * 35 + leader_factor * 30)

        elif stage == STAGE_MAIN_TREND:
            # 主升期置信度：趋势强度 + 龙头 + 情绪
            strength = indicators.get("_trend_strength", 0.0)
            strength_factor = min(1.0, strength / 1.0)
            leader_factor = min(1.0, indicators["leader_count"] / 5.0)
            sentiment_factor = min(1.0, indicators["sentiment_score"] / 80.0)
            volume_factor = min(1.0, indicators["volume_ratio_avg_5d"] / 2.0)
            score = (
                strength_factor * 30
                + leader_factor * 25
                + sentiment_factor * 25
                + volume_factor * 20
            )

        elif stage == STAGE_DISTRIBUTION:
            # 分歧期置信度：回撤 + 背离 + 动量衰减
            drawdown_factor = min(1.0, abs(indicators["drawdown_from_peak"]) / 0.25)
            divergence_factor = 1.0 if indicators["volume_price_divergence"] else 0.3
            decay_factor = min(
                1.0, abs(indicators["momentum_divergence"]) / 0.05,
            ) if indicators["momentum_divergence"] < 0 else 0.0
            score = (drawdown_factor * 35 + divergence_factor * 35 + decay_factor * 30)

        elif stage == STAGE_DEATH:
            # 退潮期置信度：不活跃程度 + 缩量 + 无催化
            inactive_factor = 1.0 - indicators["active_ratio"]
            volume_factor = 1.0 - indicators["volume_vs_peak"]
            catalyst_factor = min(
                1.0, indicators["days_since_last_catalyst"] / 90.0,
            )
            score = (inactive_factor * 35 + volume_factor * 35 + catalyst_factor * 30)

        else:
            score = 50.0

        return max(0.0, min(100.0, score))

    def _predict_next_stage(
        self,
        current_stage: str,
        indicators: Dict[str, Any],
    ) -> Optional[str]:
        """根据当前阶段和领先指标预测下一阶段.

        Args:
            current_stage: 当前阶段.
            indicators: 指标字典.

        Returns:
            下一阶段名称，若稳定则返回 None.
        """
        if current_stage == STAGE_BIRTH:
            # birth -> growth: momentum_20d > 5% 持续 5+ 天
            if (
                indicators["momentum_positive_days_20d"] >= 5
                and indicators["momentum_20d"] >= 0.05
            ):
                return STAGE_GROWTH

        elif current_stage == STAGE_GROWTH:
            # growth -> main_trend: 动量加速 + 量扩张 + 龙头增加
            if (
                indicators["momentum_20d"] >= 0.10
                and indicators["volume_expansion"] >= 1.2
                and indicators["leader_count_change_5d"] >= 0
                and indicators["sentiment_score"] >= 60
            ):
                return STAGE_MAIN_TREND

        elif current_stage == STAGE_MAIN_TREND:
            # main_trend -> distribution: 出现分歧信号
            if (
                indicators["volume_price_divergence"]
                or indicators["momentum_divergence"] < -0.03
                or indicators["drawdown_from_peak"] <= -0.10
            ):
                return STAGE_DISTRIBUTION

        elif current_stage == STAGE_DISTRIBUTION:
            # distribution -> death: 持续无催化
            if indicators["days_since_last_catalyst"] >= 30:
                return STAGE_DEATH

        return None

    @staticmethod
    def _days_in_current_stage(
        history: List[Dict[str, Any]],
        current_stage: str,
    ) -> int:
        """计算当前阶段已持续的交易天数.

        通过追溯历史记录中首次判定为该阶段的交易日的天数差来确定.

        Args:
            history: 历史数据列表（含 lifecycle_stage 字段）.
            current_stage: 当前阶段名称.

        Returns:
            持续天数.
        """
        if not history:
            return 0

        # 从后往前找，找到第一个不同阶段的日期
        stage_start_idx = 0
        for i in range(len(history) - 1, -1, -1):
            stage = history[i].get("lifecycle_stage", "")
            if stage != current_stage:
                stage_start_idx = i + 1
                break

        # 计算从 stage_start_idx 到当前的天数
        if stage_start_idx >= len(history):
            return 1

        days = len(history) - stage_start_idx
        return max(1, days)
