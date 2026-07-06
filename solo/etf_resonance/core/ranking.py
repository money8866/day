"""Ranking Engine - Part 8 of the Resonance System.

Composite Score = 
  ETF Trend 30% + Persistence 10% + Leader Score 25%
  + Resonance 20% + Leader Persistence 10% - Risk 5%

Final output: The Institutional Mainline Leader Ranking
《机构主线龙头排行榜》
"""

import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from etf_resonance.utils.indicators import normalize, new_high_count
from etf_resonance.utils.helpers import timeit, Config
from etf_resonance.core.trend import TrendResult
from etf_resonance.core.persistence import PersistenceResult
from etf_resonance.core.leader import LeaderResult
from etf_resonance.core.resonance import ResonanceResult
from etf_resonance.core.risk import RiskResult

logger = logging.getLogger(__name__)


@dataclass
class CompositeResult:
    """Final composite ranking result for one stock."""
    rank: int
    etf_code: str
    etf_name: str
    theme: str
    ts_code: str
    name: str
    composite_score: float     # 0-100
    trend_score: float
    persistence_score: float
    leader_score: float
    resonance_score: float
    leader_persistence: float
    risk_score: float
    leader_rank_in_etf: int
    leader_persistence_rank: float
    confidence: str            # HIGH / MEDIUM / LOW


@dataclass
class RankingOutput:
    """The final ranking output."""
    results: List[CompositeResult]
    top_n: int
    timestamp: str


class RankingEngine:
    """Compute final composite scores and produce the mainline leader ranking."""

    def __init__(self, config: Optional[Config] = None):
        cfg = config.get("composite", {}) if config else {}
        self.etf_w = cfg.get("etf_trend_weight", 0.30)
        self.persist_w = cfg.get("persistence_weight", 0.10)
        self.leader_w = cfg.get("leader_score_weight", 0.25)
        self.res_w = cfg.get("resonance_weight", 0.20)
        self.lp_w = cfg.get("leader_persistence_weight", 0.10)
        self.risk_w = cfg.get("risk_penalty_weight", -0.05)

        general_cfg = config.get("general") if config else None
        self.top_n = general_cfg.get("top_n", 20) if general_cfg else 20

    @timeit
    def rank(
        self,
        resonance_results: Dict[str, List[ResonanceResult]],
        trend_results: Dict[str, TrendResult],
        persistence_results: Dict[str, PersistenceResult],
        leader_results: Dict[str, List[LeaderResult]],
        risk_results: Dict[str, RiskResult],
        etf_theme_map: Optional[Dict[str, str]] = None,
    ) -> List[CompositeResult]:
        """Compute final composite scores and rank all candidates."""
        all_items: List[CompositeResult] = []

        for etf_code, res_list in resonance_results.items():
            etf_trend = trend_results.get(etf_code)
            etf_persistence = persistence_results.get(etf_code)
            etf_leaders = leader_results.get(etf_code, [])

            if etf_trend is None:
                continue

            etf_trend_score = etf_trend.trend_score
            etf_persist_score = etf_persistence.persistence_score if etf_persistence else 50.0

            theme = (etf_theme_map or {}).get(etf_code, "")

            for rr in res_list:
                # Find corresponding leader result
                leader_found = None
                for lr in etf_leaders:
                    if lr.ts_code == rr.ts_code:
                        leader_found = lr
                        break

                if leader_found is None:
                    continue

                # Risk
                risk = risk_results.get(rr.ts_code)
                risk_score = risk.risk_score if risk else 30.0

                # Leader Persistence (proxy using rank stability)
                lp = self._compute_leader_persistence(leader_found)

                composite_raw = (
                    self.etf_w * etf_trend_score +
                    self.persist_w * etf_persist_score +
                    self.leader_w * leader_found.leader_score +
                    self.res_w * rr.resonance_score +
                    self.lp_w * lp +
                    self.risk_w * risk_score
                )
                composite = np.clip(composite_raw, 0, 100)

                # Confidence level
                if composite >= 75 and risk_score < 40:
                    confidence = "HIGH"
                elif composite >= 55:
                    confidence = "MEDIUM"
                else:
                    confidence = "LOW"

                all_items.append(CompositeResult(
                    rank=0,
                    etf_code=etf_code,
                    etf_name=rr.etf_name,
                    theme=theme,
                    ts_code=rr.ts_code,
                    name=rr.name,
                    composite_score=round(float(composite), 1),
                    trend_score=etf_trend_score,
                    persistence_score=etf_persist_score,
                    leader_score=leader_found.leader_score,
                    resonance_score=rr.resonance_score,
                    leader_persistence=round(float(lp), 1),
                    risk_score=risk_score,
                    leader_rank_in_etf=leader_found.rank_in_etf,
                    leader_persistence_rank=0.0,
                    confidence=confidence,
                ))

        # Sort by composite descending
        all_items.sort(key=lambda x: -x.composite_score)
        for i, item in enumerate(all_items):
            item.rank = i + 1

        return all_items

    def _compute_leader_persistence(self, lr: LeaderResult) -> float:
        """Leader persistence score (0-100).
        
        Proxy: combination of rank, stability and drawdown resistance.
        Higher = more likely to persist as leader.
        """
        rank_score_val = max(0, 100 - (lr.rank_in_etf - 1) * 10)
        stability_score = lr.trend_stability if not np.isnan(lr.trend_stability) else 50
        dd_penalty = max(0, 100 - lr.drawdown * 3)

        return 0.4 * rank_score_val + 0.3 * stability_score + 0.3 * dd_penalty

    def to_dataframe(self, results: List[CompositeResult]) -> pd.DataFrame:
        """Convert ranking results to a pandas DataFrame."""
        rows = []
        for r in results:
            rows.append({
                "Rank": r.rank,
                "ETF": r.etf_name,
                "Theme": r.theme,
                "Stock": r.name,
                "Code": r.ts_code,
                "Composite": r.composite_score,
                "Trend": r.trend_score,
                "Persistence": r.persistence_score,
                "Leader": r.leader_score,
                "Resonance": r.resonance_score,
                "LeaderPersist": r.leader_persistence,
                "Risk": r.risk_score,
                "Confidence": r.confidence,
                "ETF_Rank": r.leader_rank_in_etf,
            })
        return pd.DataFrame(rows)

    def format_report(self, results: List[CompositeResult], top_n: int = 20) -> str:
        """Format the final ranking as a readable report string."""
        report = []
        report.append("=" * 80)
        report.append("🏆  机构主线龙头排行榜 (Institutional Mainline Leader Ranking)")
        report.append("=" * 80)
        report.append("")
        report.append(f"{'Rank':<6}{'Stock':<16}{'Code':<14}{'ETF':<18}{'Theme':<16}"
                      f"{'Comp':<8}{'Trend':<8}{'Lead':<8}{'Reson':<8}{'Risk':<8}Confidence")
        report.append("-" * 120)

        for r in results[:top_n]:
            report.append(
                f"{r.rank:<6}{r.name:<16}{r.ts_code:<14}{r.etf_name:<18}{r.theme:<16}"
                f"{r.composite_score:<8.1f}{r.trend_score:<8.1f}{r.leader_score:<8.1f}"
                f"{r.resonance_score:<8.1f}{r.risk_score:<8.1f}{r.confidence}"
            )

        report.append("")
        report.append("-" * 80)
        report.append(f"Total candidates: {len(results)} | Top {min(top_n, len(results))} shown")
        report.append(f"Scoring formula: Composite = ETF_Trend({self.etf_w*100:.0f}%) "
                      f"+ Persistence({self.persist_w*100:.0f}%) "
                      f"+ Leader({self.leader_w*100:.0f}%) "
                      f"+ Resonance({self.res_w*100:.0f}%) "
                      f"+ LeaderPersist({self.lp_w*100:.0f}%) "
                      f"- Risk({abs(self.risk_w*100):.0f}%)")
        report.append("=" * 80)

        return "\n".join(report)
