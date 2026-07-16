#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Step 7: Expected Rank Model 预期排名模型
===========================================
不计算Alpha分数，而是预测未来ETF排名。

Output:
  - Predicted Rank (预测排名)
  - Probability Top1 (Top1概率)
  - Probability Top3 (Top3概率)
  - Probability Top5 (Top5概率)
  - Expected Holding Days (预期持仓天数)
  - Expected Max Drawdown (预期最大回撤)
  - Expected Volatility (预期波动率)
  - Expected Sharpe (预期Sharpe)
  - Confidence (置信度)

硬过滤器: ProbabilityTop3 >= 60%
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from etf_winner_prediction.indicators import (
    volatility, max_drawdown, sharpe_ratio,
)


@dataclass
class ExpectedRankResult:
    etf_code: str = ""
    predicted_rank: int = 0
    probability_top1: float = 0.0
    probability_top3: float = 0.0
    probability_top5: float = 0.0
    expected_holding_days: int = 0
    expected_max_drawdown: float = 0.0
    expected_volatility: float = 0.0
    expected_sharpe: float = 0.0
    confidence: float = 0.0
    reasons: list = field(default_factory=list)


class ExpectedRankModel:
    """预期排名模型 - Step 7"""

    def __init__(self, config: dict):
        self.cfg = config.get("expected_rank", {})
        self.target_ranks = self.cfg.get("target_ranks", [1, 3, 5])
        self.min_top3_prob = self.cfg.get("min_probability_top3", 0.60)

    def predict(self, etf_code: str, etf_trend_score: float,
                theme_forecast_rank: int, theme_forecast_score: float,
                leader_score: float, market_score: float,
                expected_return: float, risk_score: float,
                remaining_days: int, rotation_prob: float,
                etf_df: Optional[pd.DataFrame] = None) -> ExpectedRankResult:
        """预测ETF的排名"""
        r = ExpectedRankResult(etf_code=etf_code)

        # 综合实力分
        strength = (
            (100 - theme_forecast_rank * 10) * 0.25 +
            theme_forecast_score * 0.20 +
            etf_trend_score * 0.20 +
            leader_score * 0.15 +
            expected_return * 200 * 0.10 +
            (100 - risk_score) * 0.10
        )
        strength = float(np.clip(strength, 0, 100))

        # 预测排名
        r.predicted_rank = max(1, int(10 - strength / 10))

        # Top1/Top3/Top5概率
        r.probability_top1 = self._estimate_prob(strength, 1)
        r.probability_top3 = self._estimate_prob(strength, 3)
        r.probability_top5 = self._estimate_prob(strength, 5)

        # 预期持仓天数
        r.expected_holding_days = self._estimate_holding(remaining_days, rotation_prob, strength)

        # 预期回撤/波动率/Sharpe
        if etf_df is not None and not etf_df.empty:
            close = etf_df["close"].values.astype(float)
            r.expected_volatility = float(np.clip(volatility(close, 20), 0.05, 0.60))
            r.expected_max_drawdown = float(np.clip(max_drawdown(close[-60:]) if len(close) >= 60 else 0.1, 0.02, 0.30))
            r.expected_sharpe = float(np.clip(sharpe_ratio(close, 60), -1.0, 3.0))
        else:
            r.expected_volatility = 0.25
            r.expected_max_drawdown = 0.10
            r.expected_sharpe = 0.5

        # 置信度
        r.confidence = self._estimate_confidence(r)

        r.reasons = self._build_reasons(r)
        return r

    def _estimate_prob(self, strength, top_n) -> float:
        """估计进入TopN的概率"""
        if top_n == 1:
            base = 0.05
            if strength >= 90:
                base = 0.70
            elif strength >= 80:
                base = 0.50
            elif strength >= 70:
                base = 0.30
            elif strength >= 60:
                base = 0.15
            elif strength >= 50:
                base = 0.08
        elif top_n == 3:
            base = 0.15
            if strength >= 90:
                base = 0.90
            elif strength >= 80:
                base = 0.75
            elif strength >= 70:
                base = 0.55
            elif strength >= 60:
                base = 0.35
            elif strength >= 50:
                base = 0.20
        else:  # top 5
            base = 0.25
            if strength >= 90:
                base = 0.95
            elif strength >= 80:
                base = 0.85
            elif strength >= 70:
                base = 0.70
            elif strength >= 60:
                base = 0.50
            elif strength >= 50:
                base = 0.35
        return float(np.clip(base, 0.01, 0.99))

    def _estimate_holding(self, remaining_days, rotation_prob, strength) -> int:
        base = min(remaining_days, 60)
        if rotation_prob > 50:
            base = int(base * 0.6)
        if strength >= 80:
            base = int(base * 1.2)
        return int(np.clip(base, 20, 60))

    def _estimate_confidence(self, r: ExpectedRankResult) -> float:
        conf = 50.0
        if r.probability_top3 >= 0.70:
            conf += 20
        elif r.probability_top3 >= 0.50:
            conf += 10
        if r.probability_top1 >= 0.30:
            conf += 10
        if r.expected_sharpe >= 1.0:
            conf += 10
        if r.expected_max_drawdown < 0.10:
            conf += 10
        return float(np.clip(conf, 0, 100))

    def _build_reasons(self, r: ExpectedRankResult) -> list:
        parts = []
        parts.append(f"预测排名#{r.predicted_rank}")
        parts.append(f"Top1概率={r.probability_top1:.0%}")
        parts.append(f"Top3概率={r.probability_top3:.0%}")
        parts.append(f"预期持仓{r.expected_holding_days}天")
        parts.append(f"预期回撤={r.expected_max_drawdown*100:.1f}%")
        parts.append(f"预期Sharpe={r.expected_sharpe:.2f}")
        return parts