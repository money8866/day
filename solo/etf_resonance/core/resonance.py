"""Resonance Score - Part 5 of the Resonance System.

Measures the synergy between ETF trend strength and individual stock strength.
High resonance = stock is riding a strong ETF wave with its own alpha.

Resonance = 30% ETF Trend + 25% Relative Strength + 20% Correlation
            + 15% Relative Volume + 10% Breakout
"""

import numpy as np
from typing import Dict, List, Optional
from dataclasses import dataclass

from etf_resonance.utils.helpers import Config
from etf_resonance.core.leader import LeaderResult
from etf_resonance.core.trend import TrendResult
from etf_resonance.core.persistence import PersistenceResult


@dataclass
class ResonanceResult:
    """Per-stock resonance scoring result."""
    ts_code: str
    name: str
    etf_code: str
    etf_name: str
    resonance_score: float     # 0-100
    etf_trend_component: float
    relative_strength_component: float
    correlation_component: float
    relative_volume_component: float
    breakout_component: float


class ResonanceScorer:
    """Compute Resonance Score (0-100) for stock-ETF pairs."""

    def __init__(self, config: Optional[Config] = None):
        cfg = config.get("resonance", {}) if config else {}
        self.etf_w = cfg.get("etf_trend_weight", 0.30)
        self.rs_w = cfg.get("relative_strength_weight", 0.25)
        self.corr_w = cfg.get("correlation_weight", 0.20)
        self.rv_w = cfg.get("relative_volume_weight", 0.15)
        self.bo_w = cfg.get("breakout_weight", 0.10)

    def score(
        self,
        leader_results: Dict[str, List[LeaderResult]],
        trend_results: Dict[str, TrendResult],
        persistence_results: Optional[Dict[str, PersistenceResult]] = None,
        etf_theme_map: Optional[Dict[str, str]] = None,
    ) -> Dict[str, List[ResonanceResult]]:
        """Compute resonance scores for all stock-ETF pairs.

        Args:
            leader_results: Output from LeaderScorer {etf_code: [LeaderResult]}
            trend_results: Output from TrendScorer {etf_code: TrendResult}
            persistence_results: Optional persistence scores for ETF quality boost
            etf_theme_map: Optional {etf_code: theme_name}

        Returns:
            Dict of {etf_code: [ResonanceResult, ...]}
        """
        results: Dict[str, List[ResonanceResult]] = {}

        for etf_code, stock_results in leader_results.items():
            if etf_code not in trend_results:
                continue
            tr = trend_results[etf_code]
            etf_trend = tr.trend_score
            etf_name = f"{etf_code}/{'/'.join([v for k,v in (etf_theme_map or {}).items() if k == etf_code])}" if etf_theme_map else etf_code

            # Boost ETF trend by persistence if available
            if persistence_results and etf_code in persistence_results:
                pr = persistence_results[etf_code]
                etf_trend = 0.7 * etf_trend + 0.3 * pr.persistence_score

            etf_list = []
            for sr in stock_results:
                resonance = self._score_pair(sr, etf_trend)
                resonance.etf_name = etf_name
                etf_list.append(resonance)

            etf_list.sort(key=lambda x: -x.resonance_score)
            results[etf_code] = etf_list

        return results

    def _score_pair(self, sr: LeaderResult, etf_trend: float) -> ResonanceResult:
        """Resonance for one stock-ETF pair."""
        etf_comp = etf_trend
        rs_comp = np.clip(sr.relative_strength / 50 * 100, 0, 100) if sr.relative_strength > 0 else 0
        corr_comp = np.clip((sr.correlation + 1) / 2 * 100, 0, 100)
        rv_comp = np.clip(sr.relative_volume * 50, 0, 100)
        bo_comp = sr.leader_score  # Use leader score as breakout proxy

        resonance = (
            self.etf_w * etf_comp +
            self.rs_w * rs_comp +
            self.corr_w * corr_comp +
            self.rv_w * rv_comp +
            self.bo_w * bo_comp
        )
        resonance = np.clip(resonance, 0, 100)

        return ResonanceResult(
            ts_code=sr.ts_code,
            name=sr.name,
            etf_code=sr.etf_code,
            etf_name=sr.etf_code,
            resonance_score=round(float(resonance), 1),
            etf_trend_component=round(float(etf_comp), 1),
            relative_strength_component=round(float(rs_comp), 1),
            correlation_component=round(float(corr_comp), 1),
            relative_volume_component=round(float(rv_comp), 1),
            breakout_component=round(float(bo_comp), 1),
        )
