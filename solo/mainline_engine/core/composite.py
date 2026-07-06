"""综合评分引擎 — 聚合多模块评分，计算最终综合得分。"""

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
    volume_ratio, slope, natr, new_high_count,
    consecutive_up_days, above_ema_days, future_return,
)
from mainline_engine.core.buy import BuySignalResult, BuyEngine
from mainline_engine.core.sell import SellSignalResult


@dataclass
class CompositeResult:
    ts_code: str
    stock_name: str = ""
    etf_code: str = ""
    etf_name: str = ""
    theme: str = ""
    lifecycle_stage: str = "Unknown"

    # Input scores (normalized 0-100)
    etf_trend: float = 0.0
    capital: float = 0.0
    heat: float = 0.0
    lifecycle: float = 0.0
    leader: float = 0.0
    leader_persistence: float = 0.0
    resonance: float = 0.0
    risk_inverted: float = 0.0

    # Computed
    composite_score: float = 0.0
    confidence: str = ""
    buy_signal: str = ""
    sell_signal: str = ""
    rank: int = 0


class CompositeEngine:
    """综合评分引擎。

    聚合ETF轮动、资金流、市场热度、生命周期、龙头、龙头持续性、共振、
    风险共8个模块的评分，按预设权重加权计算0-100综合得分。
    """

    def __init__(self, config: dict):
        self.cfg = config.get('composite', {})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute(self,
                resonance_data: Dict[str, List],
                etf_scores: Dict[str, 'ETFScoreResult'],
                capital_scores: Dict[str, 'CapitalScoreResult'],
                heat_scores: Dict[str, 'HeatScoreResult'],
                lifecycle_data: Dict[str, 'LifecycleResult'],
                leader_data: Dict[str, List],
                persistence_data: Dict[str, 'PersistenceResult'],
                risk_data: Dict[str, 'RiskResult'],
                buy_signals: Dict[str, 'BuySignalResult'],
                sell_signals: Dict[str, 'SellSignalResult'],
                etf_theme_map: Dict[str, str] = None) -> List[CompositeResult]:
        """计算所有个股的综合评分。

        Parameters
        ----------
        resonance_data : dict
            {etf_code: [ResonanceResult, ...]}，共振评分结果。
        etf_scores : dict
            {etf_code: ETFScoreResult}，ETF轮动评分。
        capital_scores : dict
            {ts_code: CapitalScoreResult}，资金流评分。
        heat_scores : dict
            {ts_code: HeatScoreResult}，市场热度评分。
        lifecycle_data : dict
            {etf_code: LifecycleResult}，产业生命周期数据。
        leader_data : dict
            {etf_code: [LeaderResult, ...]}，龙头评分结果。
        persistence_data : dict
            {ts_code: PersistenceResult}，龙头持续性评分。
        risk_data : dict
            {ts_code: RiskResult}，风险评分。
        buy_signals : dict
            {ts_code: BuySignalResult}，买入信号。
        sell_signals : dict
            {ts_code: SellSignalResult}，卖出信号。
        etf_theme_map : dict, optional
            {etf_code: theme_name}，ETF所属主题映射。

        Returns
        -------
        list[CompositeResult]
            按 composite_score 降序排列。
        """
        if not resonance_data:
            logger.warning("resonance_data is empty, returning []")
            return []

        etf_scores = etf_scores or {}
        capital_scores = capital_scores or {}
        heat_scores = heat_scores or {}
        lifecycle_data = lifecycle_data or {}
        leader_data = leader_data or {}
        persistence_data = persistence_data or {}
        risk_data = risk_data or {}
        buy_signals = buy_signals or {}
        sell_signals = sell_signals or {}
        etf_theme_map = etf_theme_map or {}

        # Build per-stock lookup maps
        etf_trend_map = self._build_etf_trend_map(etf_scores)
        capital_map = self._build_score_map(capital_scores)
        heat_map = self._build_score_map(heat_scores)
        lifecycle_map = self._build_lifecycle_map(lifecycle_data)
        leader_map = self._build_leader_map(leader_data)
        persistence_map = self._build_score_map(persistence_data)
        risk_inv_map = self._build_risk_inverted_map(risk_data)
        resonance_map = self._build_resonance_map(resonance_data)

        # Collect unique stock codes from resonance_data across all ETFs
        all_ts_codes: List[str] = []
        ts_to_etf: Dict[str, str] = {}
        for etf_code, resonance_list in resonance_data.items():
            if not resonance_list:
                continue
            for rr in resonance_list:
                code = getattr(rr, 'ts_code', '')
                if code:
                    if code not in ts_to_etf:
                        all_ts_codes.append(code)
                    ts_to_etf[code] = etf_code

        if not all_ts_codes:
            logger.warning("No stock codes found in resonance_data, returning []")
            return []

        # Configurable weights
        w_etf_trend = self.cfg.get('etf_trend_weight', 0.20)
        w_capital = self.cfg.get('capital_weight', 0.20)
        w_heat = self.cfg.get('heat_weight', 0.10)
        w_lifecycle = self.cfg.get('lifecycle_weight', 0.10)
        w_leader = self.cfg.get('leader_weight', 0.15)
        w_persistence = self.cfg.get('leader_persistence_weight', 0.10)
        w_resonance = self.cfg.get('resonance_weight', 0.10)
        w_risk = self.cfg.get('risk_inverted_weight', -0.05)

        # Build arrays for vectorized scoring
        n = len(all_ts_codes)
        etf_trend_arr = np.full(n, 50.0, dtype=np.float64)
        capital_arr = np.full(n, 50.0, dtype=np.float64)
        heat_arr = np.full(n, 50.0, dtype=np.float64)
        lifecycle_arr = np.full(n, 50.0, dtype=np.float64)
        leader_arr = np.full(n, 50.0, dtype=np.float64)
        persistence_arr = np.full(n, 50.0, dtype=np.float64)
        resonance_arr = np.full(n, 50.0, dtype=np.float64)
        risk_inv_arr = np.full(n, 50.0, dtype=np.float64)

        for i, ts_code in enumerate(all_ts_codes):
            etf_code = ts_to_etf.get(ts_code, '')
            etf_trend_arr[i] = etf_trend_map.get(etf_code, 50.0)
            capital_arr[i] = capital_map.get(ts_code, 50.0)
            heat_arr[i] = heat_map.get(ts_code, 50.0)
            lifecycle_arr[i] = lifecycle_map.get(etf_code, 50.0)
            leader_arr[i] = leader_map.get(ts_code, 50.0)
            persistence_arr[i] = persistence_map.get(ts_code, 50.0)
            resonance_arr[i] = resonance_map.get(ts_code, 50.0)
            risk_inv_arr[i] = risk_inv_map.get(ts_code, 50.0)

        # Composite = weighted sum
        composite_arr = (
            etf_trend_arr * w_etf_trend +
            capital_arr * w_capital +
            heat_arr * w_heat +
            lifecycle_arr * w_lifecycle +
            leader_arr * w_leader +
            persistence_arr * w_persistence +
            resonance_arr * w_resonance +
            risk_inv_arr * w_risk
        )
        composite_arr = np.clip(composite_arr, 0.0, 100.0)

        # Classify confidence
        confidence_arr = np.where(
            composite_arr >= 80.0, 'high',
            np.where(composite_arr >= 60.0, 'medium', 'low'),
        )

        # Sort by composite_score descending
        sort_idx = np.argsort(composite_arr)[::-1]

        results: List[CompositeResult] = []
        for rank_pos, idx in enumerate(sort_idx):
            ts_code = all_ts_codes[idx]
            etf_code = ts_to_etf.get(ts_code, '')
            theme = etf_theme_map.get(etf_code, '')

            # Lifecycle stage
            lc_result = lifecycle_data.get(etf_code)
            lifecycle_stage = getattr(lc_result, 'lifecycle_stage', 'Unknown') if lc_result else 'Unknown'

            # Buy / sell signal types
            buy_sig = buy_signals.get(ts_code)
            sell_sig = sell_signals.get(ts_code)
            buy_signal_type = buy_sig.signal_type if buy_sig else ''
            sell_signal_type = sell_sig.signal_type if sell_sig else ''

            results.append(CompositeResult(
                ts_code=ts_code,
                stock_name='',
                etf_code=etf_code,
                etf_name='',
                theme=theme,
                lifecycle_stage=lifecycle_stage,
                etf_trend=round(float(etf_trend_arr[idx]), 2),
                capital=round(float(capital_arr[idx]), 2),
                heat=round(float(heat_arr[idx]), 2),
                lifecycle=round(float(lifecycle_arr[idx]), 2),
                leader=round(float(leader_arr[idx]), 2),
                leader_persistence=round(float(persistence_arr[idx]), 2),
                resonance=round(float(resonance_arr[idx]), 2),
                risk_inverted=round(float(risk_inv_arr[idx]), 2),
                composite_score=round(float(composite_arr[idx]), 2),
                confidence=str(confidence_arr[idx]),
                buy_signal=buy_signal_type,
                sell_signal=sell_signal_type,
                rank=rank_pos + 1,
            ))

        logger.info(f"CompositeEngine scored {len(results)} stocks")
        return results

    # ------------------------------------------------------------------
    # 数据映射构建
    # ------------------------------------------------------------------

    @staticmethod
    def _build_etf_trend_map(etf_scores: Dict) -> Dict[str, float]:
        """从ETFScoreResult中提取趋势得分。"""
        result: Dict[str, float] = {}
        for code, obj in etf_scores.items():
            score = getattr(obj, 'score', getattr(obj, 'etf_trend_score', 50.0))
            result[code] = float(score) if np.isfinite(score) else 50.0
        return result

    @staticmethod
    def _build_score_map(scores: Dict) -> Dict[str, float]:
        """从评分结果中提取score属性。"""
        result: Dict[str, float] = {}
        for code, obj in scores.items():
            score = getattr(obj, 'score', 50.0)
            result[code] = float(score) if np.isfinite(score) else 50.0
        return result

    @staticmethod
    def _build_lifecycle_map(lifecycle_data: Dict) -> Dict[str, float]:
        """从LifecycleResult中提取lifecycle_score。"""
        result: Dict[str, float] = {}
        for code, obj in lifecycle_data.items():
            score = getattr(obj, 'lifecycle_score', 50.0)
            result[code] = float(score) if np.isfinite(score) else 50.0
        return result

    @staticmethod
    def _build_leader_map(leader_data: Dict[str, List]) -> Dict[str, float]:
        """从LeaderResult列表中为每只个股提取leader score。"""
        result: Dict[str, float] = {}
        for etf_code, leaders in leader_data.items():
            if not leaders:
                continue
            for leader in leaders:
                code = getattr(leader, 'ts_code', '')
                if not code:
                    continue
                score = getattr(
                    leader, 'leader_score',
                    getattr(leader, 'composite_score', 50.0),
                )
                result[code] = float(score) if np.isfinite(score) else 50.0
        return result

    @staticmethod
    def _build_risk_inverted_map(risk_data: Dict) -> Dict[str, float]:
        """从RiskResult中提取risk_inverted。"""
        result: Dict[str, float] = {}
        for code, obj in risk_data.items():
            score = getattr(obj, 'risk_inverted', 50.0)
            result[code] = float(score) if np.isfinite(score) else 50.0
        return result

    @staticmethod
    def _build_resonance_map(resonance_data: Dict[str, List]) -> Dict[str, float]:
        """从ResonanceResult列表中为每只个股提取resonance_score。"""
        result: Dict[str, float] = {}
        for etf_code, resonance_list in resonance_data.items():
            if not resonance_list:
                continue
            for rr in resonance_list:
                code = getattr(rr, 'ts_code', '')
                if not code:
                    continue
                score = getattr(rr, 'resonance_score', 50.0)
                result[code] = float(score) if np.isfinite(score) else 50.0
        return result
