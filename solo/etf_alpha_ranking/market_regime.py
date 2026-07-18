#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Module G - Market Regime
========================
Measures the broad market state from the benchmark (CSI 300) and the
cross-sectional breadth of the ETF universe.

Outputs:
  market_score   (0-100)
  market_state   (Bull / Neutral / Weak / Bear)
  csi300_trend   (close vs MA20/MA60)
  market_breadth (fraction of ETFs above MA20)
  market_turnover (recent vs prior turnover ratio)
  market_volatility (20d realized vol)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict

import numpy as np
import pandas as pd

from . import indicators as ind

LOG = logging.getLogger("etf_alpha_ranking.market")


@dataclass
class MarketRegimeResult:
    market_score: float = 50.0
    market_state: str = "Neutral"
    csi300_trend: float = 0.0
    market_breadth: float = 0.0
    market_turnover: float = 1.0
    market_volatility: float = 0.0
    recommended_exposure: float = 0.5


class MarketRegimeEngine:
    def __init__(self, config: dict):
        cfg = config.get("market_regime", {})
        self.ma_short = cfg.get("ma_short", 20)
        self.ma_long = cfg.get("ma_long", 60)
        self.breadth_period = cfg.get("breadth_period", 20)
        self.turnover_period = cfg.get("turnover_period", 20)
        self.vol_period = cfg.get("vol_period", 20)
        states = cfg.get("state", {})
        self.bull_th = states.get("bull", 75)
        self.neutral_th = states.get("neutral", 50)
        self.weak_th = states.get("weak", 35)

    def score(self, benchmark_df: pd.DataFrame,
              etf_data: Dict[str, pd.DataFrame]) -> MarketRegimeResult:
        r = MarketRegimeResult()
        if benchmark_df is None or benchmark_df.empty:
            return r
        close = benchmark_df["close"].values.astype(float)
        if len(close) < self.ma_long + 1:
            return r

        ma20 = ind.sma(close, self.ma_short)
        ma60 = ind.sma(close, self.ma_long)
        last, m20, m60 = close[-1], ma20[-1], ma60[-1]
        if not (np.isfinite(m20) and np.isfinite(m60)):
            return r

        # trend: distance from MA20 + MA20 slope
        dist_ma20 = (last - m20) / (m20 + 1e-9) * 100.0
        slope20 = ind.slope(ma20, 10) * 100.0
        r.csi300_trend = float(np.clip(dist_ma20 * 5.0 + slope20 * 2.0, -50, 50))

        # breadth: fraction of ETFs above their MA20
        above = 0
        total = 0
        turnover_ratios = []
        for code, df in etf_data.items():
            if df is None or df.empty or len(df) < self.ma_short + 1:
                continue
            c = df["close"].values.astype(float)
            m = ind.sma(c, self.ma_short)
            if np.isfinite(m[-1]):
                total += 1
                if c[-1] > m[-1]:
                    above += 1
            if "amount" in df.columns and len(df) >= 2 * self.turnover_period:
                amt = df["amount"].values.astype(float)
                recent = np.mean(amt[-self.turnover_period:])
                prior = np.mean(amt[-2 * self.turnover_period:-self.turnover_period])
                if prior > 1e-9:
                    turnover_ratios.append(recent / prior)
        r.market_breadth = above / total if total else 0.0
        r.market_turnover = float(np.mean(turnover_ratios)) if turnover_ratios else 1.0
        r.market_volatility = ind.volatility(close, self.vol_period)

        # composite score
        trend_score = float(np.clip(50.0 + r.csi300_trend, 0, 100))
        breadth_score = r.market_breadth * 100.0
        turnover_score = float(np.clip((r.market_turnover - 0.8) / (1.5 - 0.8), 0, 1) * 100.0)
        vol_score = float(np.clip(100.0 - r.market_volatility * 3000.0, 0, 100))  # lower vol -> higher
        r.market_score = float(np.clip(
            0.40 * trend_score + 0.25 * breadth_score
            + 0.15 * turnover_score + 0.20 * vol_score, 0, 100))

        if r.market_score >= self.bull_th:
            r.market_state, r.recommended_exposure = "Bull", 1.0
        elif r.market_score >= self.neutral_th:
            r.market_state, r.recommended_exposure = "Neutral", 0.6
        elif r.market_score >= self.weak_th:
            r.market_state, r.recommended_exposure = "Weak", 0.3
        else:
            r.market_state, r.recommended_exposure = "Bear", 0.0
        return r
