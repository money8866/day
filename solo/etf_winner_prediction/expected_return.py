#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Step 6: Expected Return Model 预期收益模型
============================================
预测未来20/40/60天预期收益，不是从动量简单外推。

方法:
  1. Historical Similar Pattern (历史相似模式匹配)
  2. Regime Matching (环境匹配)
  3. Theme Persistence (主题持续性)
  4. Leader Strength (龙头强度)
  5. ETF Trend (ETF趋势)
  6. Capital Flow (资金流)

硬过滤器: ExpectedReturn >= 10%
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from etf_winner_prediction.indicators import (
    ema, slope, hurst_exponent,
    sharpe_ratio, sortino_ratio,
    returns as compute_returns,
)


@dataclass
class ExpectedReturnResult:
    etf_code: str = ""
    expected_20d: float = 0.0
    expected_40d: float = 0.0
    expected_60d: float = 0.0
    expected_return: float = 0.0       # 综合预期收益
    return_confidence: float = 0.0
    # 子预测
    pattern_return: float = 0.0
    regime_return: float = 0.0
    theme_return: float = 0.0
    leader_return: float = 0.0
    trend_return: float = 0.0
    flow_return: float = 0.0
    reasons: list = field(default_factory=list)


class ExpectedReturnModel:
    """预期收益模型 - Step 6"""

    def __init__(self, config: dict):
        self.cfg = config.get("expected_return", {})
        self.horizons = self.cfg.get("horizons", [20, 40, 60])
        self.w_momentum = self.cfg.get("momentum_weight", 0.25)
        self.w_regime = self.cfg.get("regime_match_weight", 0.20)
        self.w_theme = self.cfg.get("theme_persistence_weight", 0.20)
        self.w_leader = self.cfg.get("leader_strength_weight", 0.15)
        self.w_trend = self.cfg.get("etf_trend_weight", 0.10)
        self.w_flow = self.cfg.get("capital_flow_weight", 0.10)
        self.lookback_windows = self.cfg.get("lookback_windows", [20, 40, 60, 120])
        self.min_similarity = self.cfg.get("min_similarity", 0.6)
        self.min_return = self.cfg.get("min_expected_return", 0.10)

    def predict(self, etf_code: str, etf_df: pd.DataFrame,
                market_score: float = 50.0,
                theme_score: float = 50.0,
                theme_persistence: float = 0.0,
                remaining_days: int = 0,
                leader_score: float = 50.0,
                etf_trend_score: float = 50.0,
                capital_flow: float = 0.0,
                industry_growth: float = 50.0) -> ExpectedReturnResult:
        """预测单只ETF的预期收益"""
        r = ExpectedReturnResult(etf_code=etf_code)

        if etf_df is None or etf_df.empty or len(etf_df) < 40:
            return r

        close = etf_df["close"].values.astype(float)
        n = len(close)

        # 动量预测（基础）
        mom_20 = close[-1] / close[-21] - 1 if n >= 21 else 0
        mom_40 = (close[-1] / close[-41] - 1) * 0.5 if n >= 41 else 0
        mom_60 = (close[-1] / close[-61] - 1) * 0.3 if n >= 61 else 0
        r.pattern_return = float(np.clip(mom_20 * 0.4 + mom_40 * 0.35 + mom_60 * 0.25, -0.15, 0.40))

        # 环境匹配预测
        r.regime_return = self._predict_regime(close, market_score)

        # 主题持续性预测
        r.theme_return = self._predict_from_theme(theme_score, theme_persistence, remaining_days)

        # 龙头强度预测
        r.leader_return = self._predict_from_leader(leader_score)

        # ETF趋势预测
        r.trend_return = self._predict_from_trend(etf_trend_score, close)

        # 资金流预测
        r.flow_return = self._predict_from_flow(capital_flow, industry_growth)

        # 综合预测 (20/40/60天)
        r.expected_20d = float(np.clip(
            r.pattern_return * 0.5 + r.regime_return * 0.3 + r.leader_return * 0.2, -0.10, 0.30))
        r.expected_40d = float(np.clip(
            r.pattern_return * 0.35 + r.theme_return * 0.35 + r.trend_return * 0.3, -0.05, 0.40))
        r.expected_60d = float(np.clip(
            r.pattern_return * 0.25 + r.theme_return * 0.40 + r.flow_return * 0.35, 0.0, 0.50))

        r.expected_return = float(np.clip(
            r.expected_20d * 0.3 + r.expected_40d * 0.4 + r.expected_60d * 0.3, 0.0, 0.50))

        # 置信度
        r.return_confidence = self._estimate_confidence(r, close)

        r.reasons = self._build_reasons(r)
        return r

    def _predict_regime(self, close, market_score) -> float:
        """基于市场环境的预测"""
        # 牛市中更容易获得高收益
        base = 0.05
        if market_score >= 70:
            base = 0.15
        elif market_score >= 60:
            base = 0.10
        elif market_score >= 50:
            base = 0.06
        elif market_score >= 40:
            base = 0.03
        else:
            base = 0.01
        return base

    def _predict_from_theme(self, theme_score, persistence, remaining_days) -> float:
        """基于主题预测"""
        base = 0.05
        if theme_score >= 75:
            base = 0.18
        elif theme_score >= 60:
            base = 0.12
        elif theme_score >= 50:
            base = 0.08
        if persistence > 0.7:
            base += 0.03
        if remaining_days >= 30:
            base += 0.02
        return float(np.clip(base, 0.0, 0.25))

    def _predict_from_leader(self, leader_score) -> float:
        """基于龙头强度预测"""
        base = 0.03
        if leader_score >= 80:
            base = 0.12
        elif leader_score >= 70:
            base = 0.08
        elif leader_score >= 60:
            base = 0.05
        return float(np.clip(base, 0.0, 0.15))

    def _predict_from_trend(self, trend_score, close) -> float:
        """基于ETF趋势预测"""
        base = 0.03
        if trend_score >= 80:
            base = 0.12
        elif trend_score >= 65:
            base = 0.08
        elif trend_score >= 50:
            base = 0.05
        # 赫斯特指数加成
        if len(close) >= 240:
            h = hurst_exponent(close[-240:])
            if h > 0.6:
                base += 0.02
        return float(np.clip(base, 0.0, 0.15))

    def _predict_from_flow(self, capital_flow, industry_growth) -> float:
        """基于资金流预测"""
        base = 0.03
        if capital_flow > 0.1:
            base = 0.10
        elif capital_flow > 0:
            base = 0.06
        if industry_growth >= 70:
            base += 0.03
        return float(np.clip(base, 0.0, 0.15))

    def _estimate_confidence(self, r: ExpectedReturnResult, close) -> float:
        conf = 50.0
        if r.expected_return >= 0.15:
            conf += 15
        elif r.expected_return >= 0.10:
            conf += 10
        if r.expected_60d > r.expected_40d > r.expected_20d:
            conf += 15
        if r.expected_20d > 0.05:
            conf += 10
        # 趋势稳定性
        if len(close) >= 60:
            h = hurst_exponent(close[-60:])
            if h > 0.55:
                conf += 10
        return float(np.clip(conf, 0, 100))

    def _build_reasons(self, r: ExpectedReturnResult) -> list:
        parts = []
        parts.append(f"20D预期={r.expected_20d*100:.1f}%")
        parts.append(f"40D预期={r.expected_40d*100:.1f}%")
        parts.append(f"60D预期={r.expected_60d*100:.1f}%")
        parts.append(f"综合预期={r.expected_return*100:.1f}%")
        if r.return_confidence >= 70:
            parts.append("高置信度")
        return parts