"""产业生命周期引擎 — 识别行业所处的生命周期阶段并打分。"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from loguru import logger

from mainline_engine.core.indicators import (
    ema, sma, rma, atr, adx, rsi, macd, bollinger, kdj,
    rank_score, normalize, zscore, winsorize,
    max_drawdown, sharpe_ratio, calmar_ratio,
    rolling_corr, beta as rolling_beta,
    new_high_count, consecutive_up_days, above_ema_days,
    volume_ratio, slope, hurst_exponent, natr,
)


@dataclass
class LifecycleResult:
    etf_code: str
    lifecycle_stage: str = "Unknown"
    lifecycle_score: float = 0.0
    trend_stage_score: float = 0.0
    capital_stage_score: float = 0.0
    volume_stage_score: float = 0.0
    limit_up_stage_score: float = 0.0
    leader_height_score: float = 0.0
    heat_stage_score: float = 0.0


_STAGE_MAP = [
    (80.0, "Birth"),
    (60.0, "Expansion"),
    (40.0, "Acceleration"),
    (20.0, "Climax"),
    (10.0, "Distribution"),
    (0.0, "Decline"),
]


class LifecycleEngine:
    """产业生命周期引擎。

    对每只 ETF / 行业板块识别生命周期阶段并计算 0-100 综合得分。
    阶段映射: 100=Birth → 80=Expansion → 60=Acceleration → 40=Climax → 20=Distribution → 0=Decline
    """

    def __init__(self, config: dict):
        self.cfg = config.get('lifecycle', {})
        self._log_config()

    def _log_config(self) -> None:
        logger.debug(f"LifecycleEngine config: trend_weight={self.cfg.get('trend_stage_weight', 0.25)}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(self,
              etf_data: Dict[str, pd.DataFrame],
              etf_scores: Dict[str, 'ETFScoreResult'],
              capital_scores: Dict[str, 'CapitalScoreResult'],
              heat_scores: Dict[str, 'HeatScoreResult'],
              limit_up_data: Dict[str, pd.DataFrame] = None,
              ) -> Dict[str, LifecycleResult]:
        """对所有 ETF 的产业生命周期进行打分。

        Parameters
        ----------
        etf_data : dict
            {ts_code: DataFrame}，需含列 [trade_date, open, high, low, close, vol]。
        etf_scores : dict
            Module 1 ETF 轮动评分结果 {ts_code: ETFScoreResult}。
        capital_scores : dict
            Module 2 资金流评分结果 {ts_code: CapitalScoreResult}。
        heat_scores : dict
            Module 3 热度评分结果 {ts_code: HeatScoreResult}。
        limit_up_data : dict, optional
            {ts_code: DataFrame} 涨停数据。

        Returns
        -------
        dict[str, LifecycleResult]
        """
        limit_up_data = limit_up_data or {}

        if not etf_data:
            logger.warning("etf_data is empty, returning {}")
            return {}

        fast = self.cfg.get('lookback_short', 20)
        mid = self.cfg.get('lookback_mid', 60)
        slow = self.cfg.get('lookback_long', 120)
        adx_p = 14

        metrics_list: List[dict] = []

        for ts_code, df in etf_data.items():
            if df is None or df.empty:
                continue
            min_rows = max(slow, mid) + 5
            if len(df) < min_rows:
                logger.debug(f"Skipping {ts_code}: insufficient rows ({len(df)} < {min_rows})")
                continue

            try:
                metrics = self._compute_lifecycle_metrics(
                    ts_code, df, limit_up_data.get(ts_code),
                    etf_scores.get(ts_code),
                    capital_scores.get(ts_code),
                    heat_scores.get(ts_code),
                    fast, mid, slow, adx_p,
                )
                if metrics is not None:
                    metrics_list.append(metrics)
            except Exception as exc:
                logger.error(f"Error computing lifecycle for {ts_code}: {exc}")
                continue

        if not metrics_list:
            logger.warning("No ETF passed lifecycle computation, returning {}")
            return {}

        # 提取各子分数向量做横截面归一化
        n = len(metrics_list)
        trend_raw = np.array([m['trend_raw'] for m in metrics_list], dtype=np.float64)
        capital_raw = np.array([m['capital_raw'] for m in metrics_list], dtype=np.float64)
        volume_raw = np.array([m['volume_raw'] for m in metrics_list], dtype=np.float64)
        limit_up_raw = np.array([m['limit_up_raw'] for m in metrics_list], dtype=np.float64)
        leader_height_raw = np.array([m['leader_height_raw'] for m in metrics_list], dtype=np.float64)
        heat_raw = np.array([m['heat_raw'] for m in metrics_list], dtype=np.float64)

        trend_s = self._to_score(winsorize(trend_raw, 0.01))
        capital_s = self._to_score(winsorize(capital_raw, 0.01))
        volume_s = self._to_score(winsorize(volume_raw, 0.01))
        limit_up_s = self._to_score(winsorize(limit_up_raw, 0.01))
        leader_height_s = self._to_score(winsorize(leader_height_raw, 0.01))
        heat_s = self._to_score(winsorize(heat_raw, 0.01))

        w_trend = self.cfg.get('trend_stage_weight', 0.25)
        w_capital = self.cfg.get('capital_stage_weight', 0.20)
        w_volume = self.cfg.get('volume_stage_weight', 0.15)
        w_limit = self.cfg.get('limit_up_stage_weight', 0.15)
        w_height = self.cfg.get('leader_height_weight', 0.15)
        w_heat = self.cfg.get('heat_stage_weight', 0.10)

        final_scores = (
            trend_s * w_trend +
            capital_s * w_capital +
            volume_s * w_volume +
            limit_up_s * w_limit +
            leader_height_s * w_height +
            heat_s * w_heat
        )
        final_scores = np.clip(final_scores, 0.0, 100.0)

        results: Dict[str, LifecycleResult] = {}
        for m, fs, ts, cs, vs, ls, hs, hts in zip(
            metrics_list, final_scores,
            trend_s, capital_s, volume_s,
            limit_up_s, leader_height_s, heat_s,
        ):
            etf_code = m['etf_code']
            stage = self._detect_stage(float(fs))
            results[etf_code] = LifecycleResult(
                etf_code=etf_code,
                lifecycle_stage=stage,
                lifecycle_score=round(float(fs), 2),
                trend_stage_score=round(float(ts), 2),
                capital_stage_score=round(float(cs), 2),
                volume_stage_score=round(float(vs), 2),
                limit_up_stage_score=round(float(ls), 2),
                leader_height_score=round(float(hs), 2),
                heat_stage_score=round(float(hts), 2),
            )

        logger.info(f"LifecycleEngine scored {len(results)} ETFs")
        return results

    # ------------------------------------------------------------------
    # 单 ETF 生命周期指标计算（全向量化）
    # ------------------------------------------------------------------

    def _compute_lifecycle_metrics(self,
                                   ts_code: str,
                                   df: pd.DataFrame,
                                   limit_up_df: Optional[pd.DataFrame],
                                   etf_score: Optional['ETFScoreResult'],
                                   capital_score: Optional['CapitalScoreResult'],
                                   heat_score: Optional['HeatScoreResult'],
                                   fast: int, mid: int, slow: int,
                                   adx_p: int,
                                   ) -> Optional[dict]:
        close = np.asarray(df['close'].values, dtype=np.float64)
        high = np.asarray(df['high'].values, dtype=np.float64)
        low = np.asarray(df['low'].values, dtype=np.float64)
        vol = np.asarray(df['vol'].values, dtype=np.float64)
        n = len(close)

        # ------ Trend Stage (0-100, higher=earlier) ------
        ema_f = ema(close, fast)
        ema_m = ema(close, mid)
        ema_s = ema(close, slow)
        adx_vals = adx(high, low, close, adx_p)
        slope_vals = slope(close, fast)

        bull_align = float(ema_f[-1] > ema_m[-1] > ema_s[-1]) if n > 0 else 0.0
        bear_align = float(ema_f[-1] < ema_m[-1] < ema_s[-1]) if n > 0 else 0.0
        adx_last = float(adx_vals[-1]) if n > 0 and np.isfinite(adx_vals[-1]) else 20.0
        slope_last = float(slope_vals[-1]) if n > 0 and np.isfinite(slope_vals[-1]) else 0.0

        trend_raw = self._calc_trend_stage(bull_align, bear_align, adx_last, slope_last,
                                           ema_f, ema_m, n)

        # ------ Capital Stage (从 capital_scores 映射) ------
        capital_raw = 50.0
        if capital_score is not None:
            cap = capital_score.capital_score
            if np.isfinite(cap):
                capital_raw = self._map_capital_to_lifecycle(cap)

        # ------ Volume Stage ------
        volume_raw = self._calc_volume_stage(vol, close, fast, n)

        # ------ Limit-up Stage ------
        limit_up_raw = self._calc_limit_up_stage(limit_up_df, fast)

        # ------ Leader Height (涨幅高度) ------
        leader_height_raw = self._calc_leader_height(close, n)

        # ------ Heat Stage ------
        heat_raw = 50.0
        if heat_score is not None:
            hs = heat_score.heat_score
            if np.isfinite(hs):
                heat_raw = self._map_heat_to_lifecycle(hs)

        metrics = {
            'etf_code': ts_code,
            'trend_raw': trend_raw,
            'capital_raw': capital_raw,
            'volume_raw': volume_raw,
            'limit_up_raw': limit_up_raw,
            'leader_height_raw': leader_height_raw,
            'heat_raw': heat_raw,
        }
        return metrics

    # ------------------------------------------------------------------
    # 各子维度评分逻辑
    # ------------------------------------------------------------------

    @staticmethod
    def _calc_trend_stage(bull_align: float, bear_align: float,
                          adx_last: float, slope_last: float,
                          ema_f: np.ndarray, ema_m: np.ndarray,
                          n: int) -> float:
        """根据均线排列、ADX、斜率计算趋势阶段得分 (0-100)。"""
        score = 50.0

        if bull_align > 0.5:
            score = 70.0
        elif bear_align > 0.5:
            score = 10.0
        elif n > 0 and ema_f[-1] > ema_m[-1]:
            score = 45.0
        else:
            score = 25.0

        crossover_age = LifecycleEngine._crossover_age(ema_f, ema_m, n)
        if bull_align > 0.5:
            if crossover_age <= 5:
                score = 85.0
            elif crossover_age <= 15:
                score = 75.0
            elif crossover_age <= 30:
                score = 65.0
            else:
                score = 55.0

        if adx_last < 20:
            if bull_align > 0.5:
                score += 5.0
            else:
                score -= 10.0
        elif 25 <= adx_last <= 40:
            if bull_align > 0.5:
                score += 10.0
        elif adx_last > 45:
            if bull_align > 0.5:
                score -= 15.0
                if slope_last > 1.5:
                    score -= 10.0

        if slope_last > 2.0:
            score -= 15.0
        elif slope_last > 1.0:
            score -= 5.0
        elif slope_last < -0.5:
            score -= 20.0

        return np.clip(score, 0.0, 100.0)

    @staticmethod
    def _crossover_age(ema_f: np.ndarray, ema_m: np.ndarray, n: int) -> int:
        """EMA_fast > EMA_mid 的连续天数，表示交叉后的年龄。"""
        if n < 2:
            return 999
        condition = ema_f > ema_m
        s = pd.Series(condition.astype(int))
        groups = (~condition).cumsum()
        result = s.groupby(groups).cumcount() + 1
        result = result.where(condition, 0).values.astype(np.int32)
        return int(result[-1]) if n > 0 else 999

    @staticmethod
    def _map_capital_to_lifecycle(capital_score: float) -> float:
        if capital_score >= 80:
            return 30.0
        elif capital_score >= 65:
            return 50.0
        elif capital_score >= 50:
            return 65.0
        elif capital_score >= 35:
            return 75.0
        elif capital_score >= 20:
            return 50.0
        else:
            return 20.0

    @staticmethod
    def _calc_volume_stage(vol: np.ndarray, close: np.ndarray,
                           period: int, n: int) -> float:
        if n < period + 5:
            return 50.0
        vr = volume_ratio(vol, period)
        vr_valid = vr[np.isfinite(vr)]
        if len(vr_valid) == 0:
            return 50.0
        vr_last = float(vr_valid[-1])

        vol_ema_val = ema(vol, period)
        vol_trend = slope(vol_ema_val, period)
        vol_trend_last = float(vol_trend[-1]) if n > 0 and np.isfinite(vol_trend[-1]) else 0.0

        score = 50.0
        if vr_last < 0.7:
            score = 30.0
        elif vr_last < 1.0:
            score = 45.0
        elif vr_last < 1.5:
            score = 60.0
        elif vr_last < 2.5:
            score = 45.0
        else:
            score = 25.0

        if vol_trend_last > 0.01:
            score += 10.0
        elif vol_trend_last < -0.01:
            score -= 10.0

        return np.clip(score, 0.0, 100.0)

    @staticmethod
    def _calc_limit_up_stage(limit_up_df: Optional[pd.DataFrame],
                             period: int) -> float:
        if limit_up_df is None or limit_up_df.empty:
            return 50.0

        limit_count = 0.0
        if 'limit_status' in limit_up_df.columns:
            status = np.asarray(limit_up_df['limit_status'].tail(period), dtype=np.float64)
            status = status[np.isfinite(status)]
            limit_count = float(np.sum(status >= 1.0))
        elif 'is_limit_up' in limit_up_df.columns:
            vals = np.asarray(limit_up_df['is_limit_up'].tail(period))
            limit_count = float(np.sum(vals))
        elif 'is_zt' in limit_up_df.columns:
            vals = np.asarray(limit_up_df['is_zt'].tail(period))
            limit_count = float(np.sum(vals))

        if limit_count <= 0:
            return 40.0
        elif limit_count <= 2:
            return 60.0
        elif limit_count <= 5:
            return 70.0
        elif limit_count <= 10:
            return 50.0
        else:
            return 30.0

    @staticmethod
    def _calc_leader_height(close: np.ndarray, n: int) -> float:
        if n < 60:
            return 50.0
        low_60 = pd.Series(close).rolling(60, min_periods=1).min().values
        high_60 = pd.Series(close).rolling(60, min_periods=1).max().values
        rise_pct = (close[-1] - low_60[-1]) / max(low_60[-1], 1e-10) * 100.0
        range_pct = (close[-1] - low_60[-1]) / max(high_60[-1] - low_60[-1], 1e-10) * 100.0

        if rise_pct < 10:
            score = 80.0
        elif rise_pct < 25:
            score = 65.0
        elif rise_pct < 50:
            score = 50.0
        elif rise_pct < 80:
            score = 35.0
        else:
            score = 20.0

        if range_pct > 80:
            score = max(score - 15.0, 0.0)

        return score

    @staticmethod
    def _map_heat_to_lifecycle(heat_score: float) -> float:
        if heat_score >= 80:
            return 30.0
        elif heat_score >= 60:
            return 50.0
        elif heat_score >= 40:
            return 65.0
        elif heat_score >= 20:
            return 50.0
        else:
            return 30.0

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
