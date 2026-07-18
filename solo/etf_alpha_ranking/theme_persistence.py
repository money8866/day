#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Module D - Theme Persistence Engine
===================================
Theme Persistence Score =
    0.25 * Trend Stability
  + 0.25 * Breadth Expansion
  + 0.20 * Leader Persistence
  + 0.15 * Capital Consistency
  + 0.15 * Catalyst Duration
  -     Crowding Penalty

Each sub-component is normalized to a 0-100 scale. The final score is
clipped to [0, 100].

Outputs:
  theme_persistence_score
  theme_rank
  theme_state  (Strong / Normal / Weak / Cooling)
  expected_duration
  rotation_probability
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List

import numpy as np
import pandas as pd

from . import indicators as ind

LOG = logging.getLogger("etf_alpha_ranking.theme")


@dataclass
class ThemePersistenceResult:
    theme: str = ""
    theme_persistence: float = 0.0
    theme_rank: int = 0
    breadth: float = 0.0
    leader_score: float = 0.0
    theme_state: str = "Weak"
    expected_duration: float = 0.0
    rotation_probability: float = 0.0
    trend_stability: float = 0.0
    breadth_expansion: float = 0.0
    leader_persistence: float = 0.0
    capital_consistency: float = 0.0
    catalyst_duration: float = 0.0
    crowding_penalty: float = 0.0


class ThemePersistenceEngine:
    def __init__(self, config: dict):
        cfg = config.get("theme_persistence", {})
        self.w_trend = cfg.get("trend_stability_weight", 0.25)
        self.w_breadth = cfg.get("breadth_expansion_weight", 0.25)
        self.w_leader = cfg.get("leader_persistence_weight", 0.20)
        self.w_capital = cfg.get("capital_consistency_weight", 0.15)
        self.w_catalyst = cfg.get("catalyst_duration_weight", 0.15)
        self.crowding_max = cfg.get("crowding_penalty_max", 30.0)
        self.p_trend = cfg.get("trend_stability_period", 20)
        self.p_breadth = cfg.get("breadth_period", 20)
        self.p_leader = cfg.get("leader_persistence_period", 20)
        self.p_capital = cfg.get("capital_period", 20)
        self.p_catalyst = cfg.get("catalyst_period", 40)
        self.p_crowding = cfg.get("crowding_period", 20)
        self.crowding_z = cfg.get("crowding_zscore_threshold", 1.5)
        states = cfg.get("state", {})
        self.state_strong = states.get("strong", 75)
        self.state_normal = states.get("normal", 55)
        self.state_weak = states.get("weak", 40)

    def score(self, theme: str, stock_data: Dict[str, pd.DataFrame],
              breadth_score: float, leader_score: float) -> ThemePersistenceResult:
        """Score one theme.

        Args:
            theme: theme name
            stock_data: {ts_code: DataFrame} for the theme's constituent stocks
            breadth_score: 0-100 breadth expansion score (from Module F)
            leader_score: 0-100 leader persistence score (from Module E)
        """
        r = ThemePersistenceResult(theme=theme, breadth=breadth_score,
                                   leader_score=leader_score)

        if not stock_data:
            r.theme_state = "Weak"
            return r

        # ---- Trend Stability: mean positive-return consistency of constituents
        trend_scores = []
        for code, df in stock_data.items():
            if df is None or df.empty or len(df) < self.p_trend + 1:
                continue
            close = df["close"].values.astype(float)
            pct = np.diff(close) / close[:-1] * 100.0
            if len(pct) < self.p_trend:
                continue
            recent = pct[-self.p_trend:]
            up_ratio = float(np.mean(recent > 0))
            mean_ret = float(np.mean(recent))
            # stability = up-day ratio weighted by positive drift
            stability = up_ratio * 100.0
            if mean_ret > 0:
                stability = min(100.0, stability + mean_ret * 5.0)
            trend_scores.append(stability)
        r.trend_stability = float(np.mean(trend_scores)) if trend_scores else 0.0

        # ---- Breadth Expansion (passed in, 0-100)
        r.breadth_expansion = float(np.clip(breadth_score, 0, 100))

        # ---- Leader Persistence (passed in, 0-100)
        r.leader_persistence = float(np.clip(leader_score, 0, 100))

        # ---- Capital Consistency: turnover / volume trend consistency
        capital_scores = []
        for code, df in stock_data.items():
            if df is None or df.empty or "amount" not in df.columns:
                continue
            amt = df["amount"].values.astype(float)
            if len(amt) < self.p_capital + 5:
                continue
            recent = amt[-self.p_capital:]
            prior = amt[-2 * self.p_capital:-self.p_capital] if len(amt) >= 2 * self.p_capital else amt[:-self.p_capital]
            if len(prior) < 2:
                continue
            r_recent = float(np.mean(recent))
            r_prior = float(np.mean(prior))
            if r_prior < 1e-6:
                continue
            # consistency = sustained inflow (ratio capped at 2 -> 100)
            ratio = r_recent / r_prior
            score_c = float(np.clip((ratio - 0.8) / (2.0 - 0.8), 0, 1) * 100.0)
            # penalize erratic volume (high coefficient of variation)
            cv = float(np.std(recent) / (np.mean(recent) + 1e-6))
            score_c *= max(0.3, 1.0 - cv / 3.0)
            capital_scores.append(score_c)
        r.capital_consistency = float(np.mean(capital_scores)) if capital_scores else 50.0

        # ---- Catalyst Duration: how long the theme has been trending up
        # measured as the longest run of close > MA20 over the last catalyst_period
        catalyst_scores = []
        for code, df in stock_data.items():
            if df is None or df.empty or len(df) < self.p_catalyst + 20:
                continue
            close = df["close"].values.astype(float)
            ma = ind.sma(close, 20)
            seg = close[-self.p_catalyst:]
            ma_seg = ma[-self.p_catalyst:]
            above = np.sum(seg > ma_seg)
            catalyst_scores.append(float(above) / self.p_catalyst * 100.0)
        r.catalyst_duration = float(np.mean(catalyst_scores)) if catalyst_scores else 0.0

        # ---- Crowding Penalty: extreme breadth + volume surge = rotation risk
        # z-score of current breadth vs cross-sectional recent distribution proxy
        # Use amount spike + breadth over-extension
        amt_spikes = []
        for code, df in stock_data.items():
            if df is None or df.empty or "amount" not in df.columns:
                continue
            amt = df["amount"].values.astype(float)
            if len(amt) < self.p_crowding + 5:
                continue
            recent = amt[-self.p_crowding:]
            base = amt[-2 * self.p_crowding:-self.p_crowding] if len(amt) >= 2 * self.p_crowding else amt[:-self.p_crowding]
            if len(base) < 2 or np.mean(base) < 1e-6:
                continue
            spike = np.mean(recent) / np.mean(base)
            amt_spikes.append(spike)
        if amt_spikes:
            arr = np.array(amt_spikes)
            mu, sd = float(np.mean(arr)), float(np.std(arr))
            extreme = float(np.mean(arr > mu + self.crowding_z * sd)) if sd > 1e-6 else 0.0
            r.crowding_penalty = float(np.clip(extreme * self.crowding_max, 0, self.crowding_max))

        # ---- Final score
        score = (
            self.w_trend * r.trend_stability
            + self.w_breadth * r.breadth_expansion
            + self.w_leader * r.leader_persistence
            + self.w_capital * r.capital_consistency
            + self.w_catalyst * r.catalyst_duration
            - r.crowding_penalty
        )
        r.theme_persistence = float(np.clip(score, 0, 100))

        # ---- State & rotation probability
        if r.theme_persistence >= self.state_strong:
            r.theme_state = "Strong"
        elif r.theme_persistence >= self.state_normal:
            r.theme_state = "Normal"
        elif r.theme_persistence >= self.state_weak:
            r.theme_state = "Weak"
        else:
            r.theme_state = "Cooling"

        # Expected remaining duration (days) decays with state
        base_dur = {"Strong": 40, "Normal": 25, "Weak": 12, "Cooling": 5}
        r.expected_duration = float(base_dur.get(r.theme_state, 15))

        # Rotation probability: higher when crowding high or score falling
        r.rotation_probability = float(np.clip(
            r.crowding_penalty / self.crowding_max * 60.0
            + (100.0 - r.theme_persistence) * 0.4, 0, 100))
        return r

    def score_all(self, theme_stocks: Dict[str, List[str]],
                  all_stock_data: Dict[str, pd.DataFrame],
                  breadth_scores: Dict[str, float],
                  leader_scores: Dict[str, float]) -> Dict[str, ThemePersistenceResult]:
        out: Dict[str, ThemePersistenceResult] = {}
        for theme, stocks in theme_stocks.items():
            sd = {c: all_stock_data[c] for c in stocks if c in all_stock_data}
            br = breadth_scores.get(theme, 50.0)
            ls = leader_scores.get(theme, 50.0)
            out[theme] = self.score(theme, sd, br, ls)
        # rank
        ranked = sorted(out.values(), key=lambda x: x.theme_persistence, reverse=True)
        for i, r in enumerate(ranked, 1):
            r.theme_rank = i
        return out

    @staticmethod
    def to_dict(r: ThemePersistenceResult) -> dict:
        return {
            "theme": r.theme,
            "theme_persistence": round(r.theme_persistence, 2),
            "theme_rank": r.theme_rank,
            "breadth": round(r.breadth, 2),
            "leader_score": round(r.leader_score, 2),
            "theme_state": r.theme_state,
            "expected_duration": r.expected_duration,
            "rotation_probability": round(r.rotation_probability, 2),
            "trend_stability": round(r.trend_stability, 2),
            "breadth_expansion": round(r.breadth_expansion, 2),
            "leader_persistence": round(r.leader_persistence, 2),
            "capital_consistency": round(r.capital_consistency, 2),
            "catalyst_duration": round(r.catalyst_duration, 2),
            "crowding_penalty": round(r.crowding_penalty, 2),
        }
