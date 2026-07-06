"""共振引擎 — 计算个股与 ETF 的多维共振协同得分。"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from loguru import logger

from mainline_engine.core.indicators import (
    ema, sma, atr, adx, rsi, bollinger, kdj,
    rank_score, normalize, zscore, winsorize,
    max_drawdown, rolling_corr, beta as rolling_beta,
    volume_ratio, slope, natr,
)


@dataclass
class ResonanceResult:
    ts_code: str
    etf_code: str
    etf_trend_score: float = 0.0
    capital_score: float = 0.0
    lag_factor_score: float = 0.0
    correlation_score: float = 0.0
    breakout_score: float = 0.0
    market_heat_score: float = 0.0
    leader_persistence_score: float = 0.0
    resonance_score: float = 0.0


class ResonanceEngine:
    """共振引擎。

    对每个 ETF 内的龙头个股，计算该个股与所属 ETF 的多维共振协同得分。
    包含 7 个维度的子评分，加权得到 0-100 的共振得分。
    """

    def __init__(self, config: dict):
        self.cfg = config.get('resonance', {})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(self,
              leader_results: Dict[str, List],
              etf_trend_scores: Dict[str, float],
              capital_scores: Dict[str, float],
              heat_scores: Dict[str, float],
              persistence_scores: Dict[str, float],
              etf_theme_map: Dict[str, str] = None) -> Dict[str, List[ResonanceResult]]:
        """计算个股与 ETF 的多维共振协同得分。

        Parameters
        ----------
        leader_results : dict
            {etf_code: [LeaderResult, ...]}，Module 5 输出的龙头评分结果。
        etf_trend_scores : dict
            {etf_code: score}，Module 1 输出的 ETF 趋势得分。
        capital_scores : dict
            {ts_code: score}，Module 2 输出的资金流得分。
        heat_scores : dict
            {ts_code: score}，Module 3 输出的市场热度得分。
        persistence_scores : dict
            {ts_code: score}，Module 6 输出的龙头持续性得分。
        etf_theme_map : dict, optional
            {etf_code: theme_name}，ETF 所属主题映射。

        Returns
        -------
        dict[str, list[ResonanceResult]]
            {etf_code: [ResonanceResult, ...]}，按 resonance_score 降序排列。
        """
        if not leader_results:
            logger.warning("leader_results is empty, returning {}")
            return {}

        etf_theme_map = etf_theme_map or {}
        results: Dict[str, List[ResonanceResult]] = {}

        for etf_code, etf_leaders in leader_results.items():
            if not etf_leaders:
                continue

            n = len(etf_leaders)
            etf_trend_val = etf_trend_scores.get(etf_code, 50.0)

            # Build raw arrays for all sub-dimensions (fully vectorized within ETF group)
            raw_etf_trend = np.full(n, etf_trend_val, dtype=np.float64)

            raw_capital = np.array([
                capital_scores.get(r.ts_code, 50.0) for r in etf_leaders
            ], dtype=np.float64)

            raw_rs = np.array([
                getattr(r, 'lag_factor_score', 50.0) for r in etf_leaders
            ], dtype=np.float64)

            raw_corr = np.array([
                getattr(r, 'correlation_etf_score', 50.0) for r in etf_leaders
            ], dtype=np.float64)

            raw_breakout = np.array([
                getattr(r, 'bottom_stability_score', 50.0) for r in etf_leaders
            ], dtype=np.float64)

            raw_heat = np.array([
                heat_scores.get(r.ts_code, heat_scores.get(etf_code, 50.0)) for r in etf_leaders
            ], dtype=np.float64)

            raw_persistence = np.array([
                persistence_scores.get(r.ts_code, 50.0) for r in etf_leaders
            ], dtype=np.float64)

            # Cross-sectional normalization within this ETF group
            s_etf_trend = self._to_score(winsorize(raw_etf_trend, 0.01))
            s_capital = self._to_score(winsorize(raw_capital, 0.01))
            s_rs = self._to_score(winsorize(raw_rs, 0.01))
            s_corr = self._to_score(winsorize(raw_corr, 0.01))
            s_breakout = self._to_score(winsorize(raw_breakout, 0.01))
            s_heat = self._to_score(winsorize(raw_heat, 0.01))
            s_persistence = self._to_score(winsorize(raw_persistence, 0.01))

            w_etf_trend = self.cfg.get('etf_trend_weight', 0.20)
            w_capital = self.cfg.get('capital_weight', 0.15)
            w_rs = self.cfg.get('relative_strength_weight', 0.20)
            w_corr = self.cfg.get('correlation_weight', 0.15)
            w_breakout = self.cfg.get('breakout_weight', 0.10)
            w_heat = self.cfg.get('market_heat_weight', 0.10)
            w_persistence = self.cfg.get('leader_persistence_weight', 0.10)

            resonance_scores = (
                s_etf_trend * w_etf_trend +
                s_capital * w_capital +
                s_rs * w_rs +
                s_corr * w_corr +
                s_breakout * w_breakout +
                s_heat * w_heat +
                s_persistence * w_persistence
            )
            resonance_scores = np.clip(resonance_scores, 0.0, 100.0)

            etf_results: List[ResonanceResult] = []
            for i, r in enumerate(etf_leaders):
                etf_results.append(ResonanceResult(
                    ts_code=r.ts_code,
                    etf_code=etf_code,
                    etf_trend_score=round(float(s_etf_trend[i]), 2),
                    capital_score=round(float(s_capital[i]), 2),
                    lag_factor_score=round(float(s_rs[i]), 2),
                    correlation_score=round(float(s_corr[i]), 2),
                    breakout_score=round(float(s_breakout[i]), 2),
                    market_heat_score=round(float(s_heat[i]), 2),
                    leader_persistence_score=round(float(s_persistence[i]), 2),
                    resonance_score=round(float(resonance_scores[i]), 2),
                ))

            etf_results.sort(key=lambda x: x.resonance_score, reverse=True)
            results[etf_code] = etf_results

        total = sum(len(v) for v in results.values())
        logger.info(f"ResonanceEngine scored {total} stock-ETF pairs across {len(results)} ETFs")
        return results

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _to_score(arr: np.ndarray) -> np.ndarray:
        a = np.asarray(arr, dtype=np.float64)
        valid = a[np.isfinite(a)]
        if len(valid) == 0:
            return np.full_like(a, 50.0)
        mn, mx = np.nanmin(a), np.nanmax(a)
        if mx <= mn or not np.isfinite(mx - mn):
            return np.full_like(a, 50.0)
        return np.clip((a - mn) / (mx - mn) * 100.0, 0.0, 100.0)
