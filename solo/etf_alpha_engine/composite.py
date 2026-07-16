#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Final Scoring & Composite Engine 综合评分引擎
================================================
Final ETF Alpha =
   25% Theme Alpha
 + 20% Lifecycle
 + 20% ETF Trend
 + 20% Leader
 + 15% Market
 - Risk Penalty
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from etf_alpha_engine.market_regime import MarketRegimeResult
from etf_alpha_engine.theme_alpha import ThemeAlphaResult
from etf_alpha_engine.theme_lifecycle import LifecycleResult
from etf_alpha_engine.etf_ranking import ETFRankingResult
from etf_alpha_engine.leader_confirm import LeaderConfirmResult
from etf_alpha_engine.risk_engine import RiskResult


@dataclass
class FinalETFResult:
    """最终ETF结果（一行输出）"""
    etf_code: str = ""
    etf_name: str = ""
    theme: str = ""
    # 模块分数
    market_score: float = 0.0
    theme_score: float = 0.0
    lifecycle: str = ""
    lifecycle_bonus: float = 0.0
    trend_duration: int = 0
    rotation_probability: float = 0.0
    leader: str = ""
    leader_score: float = 0.0
    etf_alpha: float = 0.0
    risk_score: float = 0.0
    expected_return: float = 0.0
    expected_holding_days: int = 0
    suggested_position: float = 0.0
    # 信号
    buy: bool = False
    hold: bool = False
    sell: bool = False
    confidence: float = 0.0
    reasons: list = field(default_factory=list)
    # 详情
    market_state: str = ""
    theme_rank: int = 0
    stop_loss: float = 0.0
    take_profit: float = 0.0


class CompositeEngine:
    """综合评分引擎

    合并6个模块的独立分数，输出最终ETF Alpha分数和买卖信号。
    所有权重可参数优化。
    """

    def __init__(self, config: dict):
        self.cfg = config.get("composite", {})
        self.w_theme = self.cfg.get("theme_alpha_weight", 0.25)
        self.w_lifecycle = self.cfg.get("lifecycle_weight", 0.20)
        self.w_etf_trend = self.cfg.get("etf_trend_weight", 0.20)
        self.w_leader = self.cfg.get("leader_weight", 0.20)
        self.w_market = self.cfg.get("market_weight", 0.15)
        self.risk_penalty = self.cfg.get("risk_penalty", -0.10)
        self.etf_themes = config.get("etf_universe", {})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def compute(self,
                etf_ranking: Dict[str, ETFRankingResult],
                market_regime: MarketRegimeResult,
                theme_alpha: Dict[str, ThemeAlphaResult],
                theme_lifecycle: Dict[str, LifecycleResult],
                leader_confirm: Dict[str, LeaderConfirmResult],
                risk: Dict[str, RiskResult],
                etf_theme_map: Dict[str, str],
                ) -> List[FinalETFResult]:
        """合并所有模块分数，输出最终结果"""
        results = []

        for etf_code, etf_r in etf_ranking.items():
            theme_name = etf_theme_map.get(etf_code, etf_r.theme)

            # 主题分数（取对应主题）
            theme_r = theme_alpha.get(theme_name)
            theme_score = theme_r.theme_score if theme_r else 50.0
            theme_rank = theme_r.rank if theme_r else 99

            # 生命周期
            life_r = theme_lifecycle.get(theme_name)
            lifecycle_stage = life_r.stage if life_r else "Neutral"
            lifecycle_bonus = life_r.stage_bonus if life_r else 0.0
            trend_duration = life_r.trend_elapsed_days if life_r else 0
            rotation_prob = life_r.rotation_probability if life_r else 0.0

            # 龙头
            leader_r = leader_confirm.get(etf_code)
            leader_score = leader_r.leader_score if leader_r else 50.0
            leader_name = leader_r.core_leader if leader_r else ""

            # 风险
            risk_r = risk.get(etf_code)
            risk_score = risk_r.risk_score if risk_r else 50.0
            suggested_pos = risk_r.suggested_position if risk_r else 0.5
            stop_loss = risk_r.stop_loss if risk_r else 0.05
            take_profit = risk_r.take_profit if risk_r else 0.15

            # 市场状态调整仓位
            market_score = market_regime.market_score
            market_state = market_regime.market_state
            market_exposure = market_regime.suggested_exposure
            suggested_pos = float(np.clip(suggested_pos * market_exposure, 0, 1.0))

            # ===== 最终ETF Alpha =====
            # 生命周期用 stage_bonus 归一化到 0-100
            life_score_norm = 50.0 + lifecycle_bonus
            etf_trend_score = etf_r.etf_alpha_score

            final = (
                theme_score * self.w_theme +
                life_score_norm * self.w_lifecycle +
                etf_trend_score * self.w_etf_trend +
                leader_score * self.w_leader +
                market_score * self.w_market
            )
            # 风险惩罚
            risk_penalty_val = (risk_score - 50) * self.risk_penalty * 2
            final = final + risk_penalty_val
            final = float(np.clip(final, 0, 100))

            # 买卖信号（由 rules.py 决定，这里先置默认）
            r = FinalETFResult(
                etf_code=etf_code,
                etf_name=etf_r.etf_name,
                theme=theme_name,
                market_score=round(market_score, 1),
                market_state=market_state,
                theme_score=round(theme_score, 1),
                theme_rank=theme_rank,
                lifecycle=lifecycle_stage,
                lifecycle_bonus=round(lifecycle_bonus, 1),
                trend_duration=trend_duration,
                rotation_probability=round(rotation_prob, 1),
                leader=leader_name,
                leader_score=round(leader_score, 1),
                etf_alpha=round(final, 2),
                risk_score=round(risk_score, 1),
                expected_return=round(etf_r.expected_return, 4),
                expected_holding_days=etf_r.expected_holding_days,
                suggested_position=round(suggested_pos, 2),
                stop_loss=round(stop_loss, 4),
                take_profit=round(take_profit, 4),
                confidence=round(etf_r.confidence, 1),
                reasons=list(etf_r.reasons),
            )
            # 合并更多理由
            if theme_r:
                r.reasons.extend(theme_r.reasons)
            if life_r:
                r.reasons.extend(life_r.reasons)
            if leader_r:
                r.reasons.extend(leader_r.reasons)
            if risk_r:
                r.reasons.extend(risk_r.reasons)
            r.reasons = list(dict.fromkeys(r.reasons))  # 去重保序

            results.append(r)

        # 按ETF Alpha降序
        results.sort(key=lambda x: x.etf_alpha, reverse=True)
        return results
