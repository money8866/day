"""ETF Trend Score - Part 1 of the Resonance System.

Computes a comprehensive trend score (0-100) for each ETF based on:
- EMA alignment & slope (trend quality)
- ADX strength
- RS Relative Strength
- 60-day new-high distance
- Price performance (20d/60d returns)
- Volume trend & ATR stability

trend=60%, volume=20%, breakout=20%
"""

import numpy as np
import pandas as pd
from typing import Dict, Optional
from dataclasses import dataclass

from etf_resonance.utils.indicators import (
    ema, slope, adx, atr, rank_score, new_high_count,
    normalize, winsorize, rolling_window,
)
from etf_resonance.utils.helpers import safe_div, timeit, Config


@dataclass
class TrendResult:
    """Per-ETF trend scoring result."""
    ts_code: str
    trend_score: float      # 0-100 composite
    ema20_val: float
    ema60_val: float
    ema120_val: float
    ema20_slope: float
    ema60_slope: float
    adx_val: float
    rs_relative: float
    new_high_dist_pct: float
    return_20d: float
    return_60d: float
    vol_ema20_trend: float
    atr_pct: float
    ema20_above_ema60: bool
    ema60_slope_positive: bool
    vol_ema20_up: bool


class TrendScorer:
    """Compute ETF Trend Score (0-100)."""

    def __init__(self, config: Optional[Config] = None):
        cfg = config.get("trend", {}) if config else {}
        self.ema_fast = cfg.get("ema_fast", 20)
        self.ema_mid = cfg.get("ema_mid", 60)
        self.ema_slow = cfg.get("ema_slow", 120)
        self.adx_period = cfg.get("adx_period", 14)
        self.rs_period = cfg.get("rs_period", 60)
        self.new_high_period = cfg.get("new_high_period", 60)
        self.trend_w = cfg.get("trend_weight", 0.60)
        self.volume_w = cfg.get("volume_weight", 0.20)
        self.breakout_w = cfg.get("breakout_weight", 0.20)

    @timeit
    def score(self, etf_data: Dict[str, pd.DataFrame],
              benchmark_close: Optional[pd.Series] = None) -> Dict[str, TrendResult]:
        """Score all ETFs.

        Args:
            etf_data: {ts_code: DataFrame with OHLCV}
            benchmark_close: Benchmark close prices for RS calculation (e.g. CSI 300)

        Returns:
            Dict of {ts_code: TrendResult}
        """
        results = {}
        for code, df in etf_data.items():
            if df.empty or len(df) < self.ema_slow:
                continue
            result = self._score_single(code, df, benchmark_close)
            if result is not None:
                results[code] = result
        return results

    def _score_single(self, code: str, df: pd.DataFrame,
                      benchmark_close: Optional[pd.Series]) -> Optional[TrendResult]:
        """Score a single ETF."""
        try:
            close = df["close"].values.astype(np.float64)
            high = df["high"].values.astype(np.float64)
            low = df["low"].values.astype(np.float64)
            vol = df["vol"].values.astype(np.float64)

            ema20 = ema(close, self.ema_fast)
            ema60 = ema(close, self.ema_mid)
            ema120 = ema(close, self.ema_slow)

            ema20_slope_val = slope(close, self.ema_fast)[-1]
            ema60_slope_val = slope(close, self.ema_mid)[-1]

            adx_val = adx(high, low, close, self.adx_period)[-1]

            # RS Relative Strength vs benchmark or vs universe
            if benchmark_close is not None:
                bc = benchmark_close.values.astype(np.float64)
                min_len = min(len(close), len(bc), self.rs_period)
                stock_ret = close[-min_len:] / close[-min_len] - 1
                bench_ret = bc[-min_len:] / bc[-min_len] - 1
                rs_val = float(np.mean(stock_ret - bench_ret) * 100)
            else:
                rs_rank = rank_score(close[-self.rs_period:], self.rs_period)
                rs_val = float(rs_rank[-1])

            # Distance from 60-day high
            recent_60 = close[-self.new_high_period:]
            hh60 = np.max(recent_60)
            new_high_dist = (close[-1] / hh60 - 1) * 100

            # Returns
            ret_20d = (close[-1] / close[-min(20, len(close))] - 1) * 100 if len(close) >= 20 else 0
            ret_60d = (close[-1] / close[-min(60, len(close))] - 1) * 100 if len(close) >= 60 else 0

            # Volume trend
            vol_ema20 = ema(vol, 20)
            vol_slope = slope(vol, 20)[-1]
            vol_ratio = vol[-1] / np.maximum(vol_ema20[-1], 1)

            # ATR %
            atr_val = atr(high, low, close, 14)[-1]
            atr_pct = atr_val / np.maximum(close[-1], 1e-10) * 100

            # Boolean filters
            ema20_above_60 = ema20[-1] > ema60[-1]
            ema60_up = ema60_slope_val > 0
            vol_ema20_up = vol_slope > 0

            # ════════════════════════════════════
            # Trend Score (0-100)
            # ════════════════════════════════════

            # 1. Trend Quality (EMA alignment + ADX)
            alignment_score = 0.0
            if ema20[-1] > ema60[-1] > ema120[-1]:
                alignment_score = 100.0
            elif ema20[-1] > ema60[-1]:
                alignment_score = 70.0
            elif ema20[-1] > ema120[-1]:
                alignment_score = 40.0
            else:
                alignment_score = 10.0

            ema_slope_score = np.clip(
                (abs(ema20_slope_val) / 0.5) * 100, 0, 100
            )

            adx_score = np.clip((adx_val / 50) * 100, 0, 100)

            trend_quality = (
                0.35 * alignment_score +
                0.25 * ema_slope_score +
                0.20 * adx_score +
                0.10 * min(ret_20d * 2, 100) +
                0.10 * max(0, 100 - abs(new_high_dist) * 5)
            )

            # 2. Volume Score
            vol_ratio_score = np.clip((vol_ratio - 0.5) / 2.0 * 100, 0, 100)
            vol_slope_score = np.clip((vol_slope + 0.5) * 50, 0, 100)
            volume_score = 0.6 * vol_ratio_score + 0.4 * vol_slope_score

            # 3. Breakout Score
            rs_score = np.clip(rs_val / 2, 0, 100)
            hh_dist_score = max(0, 100 - abs(new_high_dist) * 10)
            return_60d_score = np.clip(ret_60d * 1.5, 0, 100)
            breakout_score = (
                0.35 * rs_score +
                0.30 * hh_dist_score +
                0.35 * return_60d_score
            )

            # Composite
            trend_score = (
                self.trend_w * trend_quality +
                self.volume_w * volume_score +
                self.breakout_w * breakout_score
            )
            trend_score = np.clip(trend_score, 0, 100)

            return TrendResult(
                ts_code=code,
                trend_score=round(float(trend_score), 1),
                ema20_val=round(float(ema20[-1]), 2),
                ema60_val=round(float(ema60[-1]), 2),
                ema120_val=round(float(ema120[-1]), 2),
                ema20_slope=round(float(ema20_slope_val), 4),
                ema60_slope=round(float(ema60_slope_val), 4),
                adx_val=round(float(adx_val), 1),
                rs_relative=round(float(rs_val), 1),
                new_high_dist_pct=round(float(new_high_dist), 2),
                return_20d=round(float(ret_20d), 2),
                return_60d=round(float(ret_60d), 2),
                vol_ema20_trend=round(float(vol_slope), 4),
                atr_pct=round(float(atr_pct), 2),
                ema20_above_ema60=bool(ema20_above_60),
                ema60_slope_positive=bool(ema60_up),
                vol_ema20_up=bool(vol_ema20_up),
            )

        except Exception as e:
            logger = __import__("logging").getLogger(__name__)
            logger.error(f"TrendScorer failed for {code}: {e}")
            return None

    def filter_etfs(self, results: Dict[str, TrendResult],
                    config: Optional[Config] = None) -> Dict[str, TrendResult]:
        """Filter ETFs based on minimum quality thresholds.

        Only keep ETFs that pass:
        - TrendScore > 70
        - EMA20 > EMA60
        - EMA60 slope > 0
        - ADX > 20
        - Volume EMA20 upward
        """
        cfg = config.get("etf_filter", {}) if config else {}
        ts_min = cfg.get("trend_score_min", 70)
        adx_min = cfg.get("adx_min", 20)

        filtered = {}
        for code, r in results.items():
            if r.trend_score < ts_min:
                continue
            if not r.ema20_above_ema60:
                continue
            if not r.ema60_slope_positive:
                continue
            if r.adx_val < adx_min:
                continue
            if not r.vol_ema20_up:
                continue
            filtered[code] = r
        return filtered
