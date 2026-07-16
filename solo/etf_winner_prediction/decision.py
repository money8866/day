#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Decision Engine 决策引擎
===========================
硬过滤器（全部必须满足，不满足则拒绝ETF）:
  1. MarketScore >= 60
  2. ThemeForecastRank <= 3
  3. RemainingTrendDays >= 20
  4. LeaderScore >= 75
  5. RiskScore <= 40
  6. ExpectedReturn >= 10%
  7. ProbabilityTop3 >= 60%

不满足任何一项 → Reject ETF
不使用加权补偿。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class DecisionResult:
    """决策结果"""
    accepted: bool = False
    reject_reasons: list = field(default_factory=list)
    passed_filters: list = field(default_factory=list)
    failed_filters: list = field(default_factory=list)


class DecisionEngine:
    """决策引擎 - 硬过滤器"""

    def __init__(self, config: dict):
        self.cfg = config.get("decision", {})
        self.filters = self.cfg.get("filters", {})
        self.messages = self.cfg.get("reject_messages", {})

    def evaluate(self,
                 market_score: float,
                 theme_forecast_rank: int,
                 remaining_trend_days: int,
                 leader_score: float,
                 risk_score: float,
                 expected_return: float,
                 probability_top3: float) -> DecisionResult:
        """评估ETF是否通过所有硬过滤器"""
        r = DecisionResult()

        checks = [
            ("market", market_score >= self.filters.get("market_score_min", 60),
             f"市场环境不支撑(MarketScore={market_score:.1f}<{self.filters.get('market_score_min', 60)})"),
            ("theme", theme_forecast_rank <= self.filters.get("theme_forecast_rank_max", 3),
             f"主题预测排名不足(#{theme_forecast_rank}>{self.filters.get('theme_forecast_rank_max', 3)})"),
            ("trend_days", remaining_trend_days >= self.filters.get("remaining_trend_days_min", 20),
             f"剩余趋势天数不足({remaining_trend_days}<{self.filters.get('remaining_trend_days_min', 20)})"),
            ("leader", leader_score >= self.filters.get("leader_score_min", 75),
             f"龙头强度不足({leader_score:.1f}<{self.filters.get('leader_score_min', 75)})"),
            ("risk", risk_score <= self.filters.get("risk_score_max", 40),
             f"风险过高({risk_score:.1f}>{self.filters.get('risk_score_max', 40)})"),
            ("expected_return", expected_return >= self.filters.get("expected_return_min", 0.10),
             f"预期收益不足({expected_return*100:.1f}%<{self.filters.get('expected_return_min', 0.10)*100:.0f}%)"),
            ("top3_prob", probability_top3 >= self.filters.get("probability_top3_min", 0.60),
             f"Top3概率不足({probability_top3:.0%}<{self.filters.get('probability_top3_min', 0.60):.0%})"),
        ]

        for name, passed, fail_msg in checks:
            if passed:
                r.passed_filters.append(name)
            else:
                r.failed_filters.append(name)
                r.reject_reasons.append(fail_msg)

        r.accepted = len(r.failed_filters) == 0
        return r