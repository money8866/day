"""主题轮动引擎 — 将 ETF 自动聚合成主题并排名。"""

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
class ThemeResult:
    theme_name: str
    theme_type: str = ""
    etf_codes: List[str] = None
    stock_codes: List[str] = None
    avg_resonance: float = 0.0
    avg_etf_trend: float = 0.0
    avg_capital: float = 0.0
    avg_heat: float = 0.0
    lifecycle_stage: str = "Unknown"
    composite_score: float = 0.0
    rank: int = 0


_STAGE_MAP = [
    (80.0, "Birth"),
    (60.0, "Expansion"),
    (40.0, "Acceleration"),
    (20.0, "Climax"),
    (10.0, "Distribution"),
    (0.0, "Decline"),
]


class ThemeRotationEngine:
    """主题轮动引擎。

    将 ETF 按主题分组聚合，计算每个主题的综合得分，
    自动识别 Main Theme（composite >= 70）与 Secondary Theme（composite >= 50），
    支持主题轮动策略。
    """

    def __init__(self, config: dict):
        self.cfg = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(self,
              resonance_data: Dict[str, List],
              lifecycle_data: Dict[str, 'LifecycleResult'],
              etf_theme_map: Dict[str, str],
              etf_scores: Dict[str, float] = None,
              capital_scores: Dict[str, float] = None,
              heat_scores: Dict[str, float] = None) -> Dict[str, ThemeResult]:
        """将 ETF 按主题聚类并排名。

        Parameters
        ----------
        resonance_data : dict
            {etf_code: [ResonanceResult, ...]}，Module 7 输出的共振结果。
        lifecycle_data : dict
            {etf_code: LifecycleResult}，Module 4 输出的生命周期结果。
        etf_theme_map : dict
            {etf_code: theme_name}，ETF 所属主题映射。
        etf_scores : dict, optional
            {etf_code: score}，Module 1 输出的 ETF 趋势得分。
        capital_scores : dict, optional
            {ts_code: score}，Module 2 输出的资金流得分。
        heat_scores : dict, optional
            {ts_code: score}，Module 3 输出的市场热度得分。

        Returns
        -------
        dict[str, ThemeResult]
            {theme_name: ThemeResult}，按 composite_score 降序排列。
        """
        if not etf_theme_map:
            logger.warning("etf_theme_map is empty, returning {}")
            return {}

        etf_scores = etf_scores or {}
        capital_scores = capital_scores or {}
        heat_scores = heat_scores or {}

        # 1. Group ETFs by theme name
        theme_etfs: Dict[str, List[str]] = {}
        for etf_code, theme_name in etf_theme_map.items():
            if not theme_name:
                continue
            theme_etfs.setdefault(theme_name, []).append(etf_code)

        if not theme_etfs:
            logger.warning("No themes found in etf_theme_map, returning {}")
            return {}

        # 2. Pre-index resonance scores per ETF for fast lookup
        etf_resonance_list: Dict[str, List[float]] = {}
        etf_stock_list: Dict[str, List[str]] = {}
        for etf_code, resonances in resonance_data.items():
            if resonances:
                etf_resonance_list[etf_code] = [r.resonance_score for r in resonances]
                etf_stock_list[etf_code] = [r.ts_code for r in resonances]

        theme_results: Dict[str, ThemeResult] = {}

        for theme_name, etf_codes in theme_etfs.items():
            if not etf_codes:
                continue

            # Collect all scores for this theme
            all_resonance: List[float] = []
            all_etf_trend: List[float] = []
            all_capital: List[float] = []
            all_heat: List[float] = []
            all_stock_codes: List[str] = []
            lifecycle_scores: List[float] = []

            for etf_code in etf_codes:
                etf_tr = etf_scores.get(etf_code, 50.0)
                cap = capital_scores.get(etf_code, 50.0)
                ht = heat_scores.get(etf_code, 50.0)

                lc = lifecycle_data.get(etf_code)
                if lc is not None and hasattr(lc, 'lifecycle_score'):
                    lifecycle_scores.append(lc.lifecycle_score)

                resonances = etf_resonance_list.get(etf_code, [])
                if resonances:
                    all_resonance.extend(resonances)
                    all_etf_trend.extend([etf_tr] * len(resonances))
                    all_capital.extend([cap] * len(resonances))
                    all_heat.extend([ht] * len(resonances))

                stocks = etf_stock_list.get(etf_code, [])
                all_stock_codes.extend(stocks)

            if not all_resonance:
                logger.debug(f"Theme '{theme_name}' has no resonance data, skipping")
                continue

            # Compute theme-level averages
            avg_resonance = float(np.mean(all_resonance))
            avg_etf_trend = float(np.mean(all_etf_trend))
            avg_capital = float(np.mean(all_capital))
            avg_heat = float(np.mean(all_heat))
            avg_lifecycle = float(np.mean(lifecycle_scores)) if lifecycle_scores else 50.0

            # Composite score
            w_resonance = self.cfg.get('theme_resonance_weight', 0.35)
            w_trend = self.cfg.get('theme_trend_weight', 0.25)
            w_capital = self.cfg.get('theme_capital_weight', 0.20)
            w_heat = self.cfg.get('theme_heat_weight', 0.10)
            w_lifecycle = self.cfg.get('theme_lifecycle_weight', 0.10)

            composite = (
                avg_resonance * w_resonance +
                avg_etf_trend * w_trend +
                avg_capital * w_capital +
                avg_heat * w_heat +
                avg_lifecycle * w_lifecycle
            )
            composite = float(np.clip(composite, 0.0, 100.0))

            # Classify theme type
            if composite >= 70.0:
                theme_type = "main"
            elif composite >= 50.0:
                theme_type = "secondary"
            else:
                theme_type = ""

            stage = self._detect_stage(avg_lifecycle)

            # Deduplicate stock codes while preserving order
            seen: set = set()
            unique_stocks: List[str] = []
            for s in all_stock_codes:
                if s not in seen:
                    seen.add(s)
                    unique_stocks.append(s)

            theme_results[theme_name] = ThemeResult(
                theme_name=theme_name,
                theme_type=theme_type,
                etf_codes=etf_codes[:],
                stock_codes=unique_stocks,
                avg_resonance=round(avg_resonance, 2),
                avg_etf_trend=round(avg_etf_trend, 2),
                avg_capital=round(avg_capital, 2),
                avg_heat=round(avg_heat, 2),
                lifecycle_stage=stage,
                composite_score=round(composite, 2),
                rank=0,
            )

        if not theme_results:
            logger.warning("No theme results computed, returning {}")
            return {}

        # Sort by composite_score descending and assign ranks
        sorted_themes = sorted(theme_results.values(), key=lambda x: x.composite_score, reverse=True)
        final_results: Dict[str, ThemeResult] = {}
        for rank_idx, tr in enumerate(sorted_themes):
            tr.rank = rank_idx + 1
            final_results[tr.theme_name] = tr

        logger.info(f"ThemeRotationEngine scored {len(final_results)} themes")
        return final_results

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_stage(score: float) -> str:
        for threshold, stage_name in _STAGE_MAP:
            if score >= threshold:
                return stage_name
        return "Decline"

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
