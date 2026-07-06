"""Persistence Score - Part 2 of the Resonance System.

Measures the sustainability of ETF trends:
- Past 20 days up-day count
- EMA20 continuous up days
- Consecutive new highs
- Volume amplification days
- Trend duration
- ATR stability

PersistenceScore: 0-100
"""

import numpy as np
import pandas as pd
from typing import Dict, Optional
from dataclasses import dataclass

from etf_resonance.utils.indicators import (
    ema, slope, atr, new_high_count, consecutive_up_days,
    volume_trend_days, ema_aligned_days, normalize,
)
from etf_resonance.utils.helpers import timeit, Config


@dataclass
class PersistenceResult:
    """Per-ETF persistence scoring result."""
    ts_code: str
    persistence_score: float   # 0-100
    up_days_20: int
    ema_up_days_20: int
    new_high_times: int
    vol_up_days_20: int
    trend_duration_days: int
    atr_stability: float
    consecutive_new_high: int


class PersistenceScorer:
    """Compute ETF Persistence Score (0-100).
    
    Measures how sustainable the trend is - higher means more
    persistent institutional buying.
    """

    def __init__(self, config: Optional[Config] = None):
        cfg = config.get("persistence", {}) if config else {}
        self.period = cfg.get("persistence_period", 20)
        self.up_w = cfg.get("up_days_weight", 0.20)
        self.ema_w = cfg.get("ema_up_days_weight", 0.25)
        self.nh_w = cfg.get("new_high_weight", 0.20)
        self.vol_w = cfg.get("volume_up_days_weight", 0.15)
        self.dur_w = cfg.get("trend_duration_weight", 0.10)
        self.atr_w = cfg.get("atr_stability_weight", 0.10)

    @timeit
    def score(self, etf_data: Dict[str, pd.DataFrame]) -> Dict[str, PersistenceResult]:
        """Score persistence for all ETFs."""
        results = {}
        for code, df in etf_data.items():
            if df.empty or len(df) < 60:
                continue
            result = self._score_single(code, df)
            if result is not None:
                results[code] = result
        return results

    def _score_single(self, code: str, df: pd.DataFrame) -> Optional[PersistenceResult]:
        try:
            close = df["close"].values.astype(np.float64)
            high = df["high"].values.astype(np.float64)
            low = df["low"].values.astype(np.float64)
            vol = df["vol"].values.astype(np.float64)

            P = self.period
            lookback = min(P, len(close))

            # 1. Up days in last 20
            returns = np.diff(close[-lookback - 1:]) / close[-lookback - 1:-1] * 100
            up_days = int(np.sum(returns > 0))

            # 2. EMA20 continuous up days
            ema_up_days_arr = ema_aligned_days(close, 20, 60)
            ema_up_days = int(ema_up_days_arr[-1])

            # 3. New high count
            nh_arr = new_high_count(close, 60)
            nh_times = int(np.sum(nh_arr[-lookback:]))

            # 4. Volume amplification days
            vol_ema20 = ema(vol, 20)
            vol_ratio = vol[-lookback:] / np.maximum(vol_ema20[-lookback:], 1)
            vol_up_days = int(np.sum(vol_ratio > 1.0))

            # 5. Trend duration (days since EMA20 crossed above EMA60)
            ema20 = ema(close, 20)
            ema60 = ema(close, 60)
            above = ema20 > ema60
            trend_duration = 0
            for i in range(len(above) - 1, -1, -1):
                if above[i]:
                    trend_duration += 1
                else:
                    break

            # 6. ATR stability (lower CV = more stable)
            atr_val = atr(high, low, close, 14)[-lookback:]
            atr_cv = np.std(atr_val) / np.maximum(np.mean(atr_val), 1e-10)
            atr_stability = max(0, 100 - atr_cv * 50)

            # 7. Consecutive new highs in recent period
            max_idx = int(np.argmax(close[-lookback:]))
            consecutive_nh = int(np.sum(close[-(lookback - max_idx):] >= np.maximum.accumulate(
                close[-(lookback - max_idx):]
            )))

            # ════════════════════════════════════
            # Compute Persistence Score
            # ════════════════════════════════════

            up_score = (up_days / P) * 100
            ema_up_score = min(ema_up_days * 5, 100)
            nh_score = min(nh_times * 10, 100)
            vol_up_score = (vol_up_days / P) * 100
            dur_score = min(trend_duration, 100)
            atr_score = atr_stability

            persistence = (
                self.up_w * up_score +
                self.ema_w * ema_up_score +
                self.nh_w * nh_score +
                self.vol_w * vol_up_score +
                self.dur_w * dur_score +
                self.atr_w * atr_score
            )
            persistence = np.clip(persistence, 0, 100)

            return PersistenceResult(
                ts_code=code,
                persistence_score=round(float(persistence), 1),
                up_days_20=up_days,
                ema_up_days_20=ema_up_days,
                new_high_times=nh_times,
                vol_up_days_20=vol_up_days,
                trend_duration_days=trend_duration,
                atr_stability=round(float(atr_stability), 1),
                consecutive_new_high=consecutive_nh,
            )

        except Exception as e:
            logger = __import__("logging").getLogger(__name__)
            logger.error(f"PersistenceScorer failed for {code}: {e}")
            return None
