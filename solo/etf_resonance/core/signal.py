"""Buy/Sell Signal Detection - Parts 9 & 10 of the Resonance System.

Buy Signals:
  - EMA20 retracement with volume shrinkage
  - Platform / box breakout
  - High Tight Flag
  - VCP (Volatility Contraction Pattern)
  - 52-week high breakout
  - Volume contraction consolidation

Sell Signals:
  - EMA20 breakdown
  - ATR stop hit
  - ADX weakening
  - ETF breakdown below EMA20
  - ETF Trend Score < 60
  - Leader rotation
  - Volume stagnation with price topping
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from etf_resonance.utils.indicators import (
    ema, adx, atr, slope, normalize, new_high_count,
)
from etf_resonance.utils.helpers import timeit, Config


@dataclass
class BuySignal:
    """Buy signal details."""
    signal: str                     # RETRACE_MA20 / BREAKOUT / HTF / VCP / NEW_HIGH / VOL_CONTRACT
    reason: str
    entry_price: float
    stop_loss: float
    atr_stop: float
    score: int                     # 0-100 signal strength

    @property
    def confidence(self) -> str:
        if self.score >= 80:
            return "HIGH"
        elif self.score >= 60:
            return "MEDIUM"
        return "LOW"


@dataclass
class SellSignal:
    """Sell signal details."""
    signal: str                     # EMA20_BREAK / ATR_STOP / ADX_WEAK / ETF_BREAK / TREND_DROP / LEADER_LOST / VOL_STAG
    reason: str
    exit_price: float
    score: int


class SignalDetector:
    """Detect buy and sell signals for stocks in the ranking."""

    def __init__(self, config: Optional[Config] = None):
        cfg_buy = config.get("buy_signal", {}) if config else {}
        cfg_sell = config.get("sell_signal", {}) if config else {}

        self.retrace_ma20_max = cfg_buy.get("retrace_ma20_max_pct", -1.0)
        self.retrace_vol_ratio_max = cfg_buy.get("retrace_volume_ratio_max", 0.8)
        self.breakout_vol_min = cfg_buy.get("breakout_volume_min", 1.5)
        self.vcp_count = cfg_buy.get("vcp_contraction_count", 3)
        self.htf_base_days = cfg_buy.get("htf_base_days", 15)
        self.htf_max_retrace = cfg_buy.get("htf_max_retrace", -25.0)

        self.stop_atr = cfg_sell.get("stop_atr_multiple", 2.0)
        self.adx_turn = cfg_sell.get("adx_turn_threshold", 20)
        self.etf_trend_min = cfg_sell.get("etf_trend_threshold", 60)

    @timeit
    def detect_buy(self, df: pd.DataFrame) -> Optional[BuySignal]:
        """Detect buy signals for one stock."""
        if df.empty or len(df) < 60:
            return None

        close = df["close"].values.astype(np.float64)
        high = df["high"].values.astype(np.float64)
        low = df["low"].values.astype(np.float64)
        vol = df["vol"].values.astype(np.float64)

        ema20 = ema(close, 20)
        ema60 = ema(close, 60)
        atr_val = atr(high, low, close, 14)

        latest_close = close[-1]
        latest_ema20 = ema20[-1]
        latest_atr = atr_val[-1]

        stop_price = latest_close - self.stop_atr * latest_atr

        signals = []

        # ──────────────────────────────────────────
        # 1. EMA20 Retracement (缩量回踩)
        # ──────────────────────────────────────────
        if len(close) >= 5:
            retrace_pct = (close[-1] / ema20[-1] - 1) * 100
            vol_ratio = vol[-1] / np.mean(vol[-5:-1]) if np.mean(vol[-5:-1]) > 0 else 99

            if retrace_pct <= self.retrace_ma20_max and vol_ratio <= self.retrace_vol_ratio_max:
                score = 75
                if retrace_pct >= -3:
                    score += 10
                if ema20[-1] > ema60[-1]:
                    score += 10
                if vol_ratio <= 0.6:
                    score += 5
                signals.append(BuySignal(
                    signal="RETRACE_MA20",
                    reason=f"缩量回踩EMA20: {retrace_pct:.1f}%, 量比={vol_ratio:.2f}",
                    entry_price=round(float(latest_close), 2),
                    stop_loss=round(float(stop_price), 2),
                    atr_stop=round(float(stop_price), 2),
                    score=min(score, 100),
                ))

        # ──────────────────────────────────────────
        # 2. Platform Breakout (箱体突破)
        # ──────────────────────────────────────────
        lookback = 40
        if len(close) >= lookback:
            base_high = np.max(close[-lookback:-5])
            base_low = np.min(close[-lookback:-5])
            base_range = (base_high / base_low - 1) * 100 if base_low > 0 else 0
            vol_surge = vol[-1] / np.mean(vol[-20:-5]) if np.mean(vol[-20:-5]) > 0 else 1

            if (latest_close > base_high * 1.01 and base_range < 30
                    and vol_surge >= self.breakout_vol_min):
                score = 70
                if latest_close > np.max(close[-120:]) * 0.95:
                    score += 15
                if vol_surge >= 2.0:
                    score += 10
                if ema20[-1] > ema60[-1]:
                    score += 5
                signals.append(BuySignal(
                    signal="BREAKOUT",
                    reason=f"箱体突破: 区间涨幅{base_range:.1f}%, 量比={vol_surge:.2f}",
                    entry_price=round(float(latest_close), 2),
                    stop_loss=round(float(base_high * 0.97), 2),
                    atr_stop=round(float(stop_price), 2),
                    score=min(score, 100),
                ))

        # ──────────────────────────────────────────
        # 3. High Tight Flag (高位紧凑旗形)
        # ──────────────────────────────────────────
        if len(close) >= self.htf_base_days + 30:
            prev_high = np.max(close[-(self.htf_base_days + 30):-self.htf_base_days])
            recent_range = (np.max(close[-self.htf_base_days:]) /
                            np.min(close[-self.htf_base_days:]) - 1) * 100

            if (latest_close > prev_high * 1.1 and recent_range < 15
                    and latest_close > prev_high):
                score = 80
                if recent_range < 10:
                    score += 10
                if vol[-1] > np.mean(vol[-self.htf_base_days:-1]):
                    score += 10
                signals.append(BuySignal(
                    signal="HTF",
                    reason=f"High Tight Flag: 前期拉升{((prev_high/np.min(close[-(self.htf_base_days+30):-self.htf_base_days])-1)*100):.0f}%, 回撤{recent_range:.1f}%",
                    entry_price=round(float(latest_close), 2),
                    stop_loss=round(float(np.min(close[-self.htf_base_days:])), 2),
                    atr_stop=round(float(stop_price), 2),
                    score=min(score, 100),
                ))

        # ──────────────────────────────────────────
        # 4. VCP (Volatility Contraction Pattern)
        # ──────────────────────────────────────────
        if len(close) >= 30:
            ranges = []
            for i in range(self.vcp_count):
                seg = close[-(i + 1) * 10:max(1, len(close) - i * 10)]
                if len(seg) >= 3:
                    seg_range = (np.max(seg) / np.min(seg) - 1) * 100
                    ranges.append(seg_range)

            if len(ranges) >= 2 and all(ranges[i] < ranges[i - 1] for i in range(1, len(ranges))):
                score = 75
                if len(ranges) >= 3:
                    score += 10
                if atr_val[-1] / np.mean(atr_val[-20:]) < 0.8:
                    score += 10
                signals.append(BuySignal(
                    signal="VCP",
                    reason=f"VCP波动率收缩: {'→'.join([f'{r:.1f}%' for r in ranges])}",
                    entry_price=round(float(latest_close), 2),
                    stop_loss=round(float(latest_close - latest_atr * 1.5), 2),
                    atr_stop=round(float(stop_price), 2),
                    score=min(score, 100),
                ))

        # ──────────────────────────────────────────
        # 5. 52-week / 60-day High Breakout
        # ──────────────────────────────────────────
        if len(close) >= 120:
            hh_120 = np.max(close[-120:])
            if latest_close >= hh_120 * 0.995:
                vol_ratio = vol[-1] / np.maximum(np.mean(vol[-20:-1]), 1)
                score = 70
                if latest_close >= hh_120:
                    score += 15
                if vol_ratio >= 1.3:
                    score += 10
                if ema20[-1] > ema60[-1]:
                    score += 5
                signals.append(BuySignal(
                    signal="NEW_HIGH",
                    reason=f"{(120 if len(close)>=250 else 60)}日新高突破, 量比={vol_ratio:.2f}",
                    entry_price=round(float(latest_close), 2),
                    stop_loss=round(float(latest_close - latest_atr * 2.0), 2),
                    atr_stop=round(float(stop_price), 2),
                    score=min(score, 100),
                ))

        # ──────────────────────────────────────────
        # 6. Volume Consolidation (缩量整理)
        # ──────────────────────────────────────────
        if len(close) >= 15:
            vol_base = np.mean(vol[-20:-5]) if len(vol) >= 20 else np.mean(vol[:-1])
            recent_vol = np.mean(vol[-5:])
            vol_shrink = recent_vol / np.maximum(vol_base, 1)
            price_range = (np.max(close[-10:]) / np.min(close[-10:]) - 1) * 100

            if vol_shrink <= 0.7 and price_range <= 5 and ema20[-1] > ema60[-1]:
                score = 65
                if vol_shrink <= 0.5:
                    score += 10
                if price_range <= 3:
                    score += 10
                if close[-1] > ema20[-1]:
                    score += 10
                signals.append(BuySignal(
                    signal="VOL_CONTRACT",
                    reason=f"缩量整理: 量缩{vol_shrink:.0%}, 区间{price_range:.1f}%",
                    entry_price=round(float(latest_close), 2),
                    stop_loss=round(float(np.min(close[-10:]) * 0.97), 2),
                    atr_stop=round(float(stop_price), 2),
                    score=min(score, 100),
                ))

        return max(signals, key=lambda x: x.score) if signals else None

    def detect_sell(self, df: pd.DataFrame,
                    etf_trend_score: float = 100.0,
                    etf_df: Optional[pd.DataFrame] = None) -> Optional[SellSignal]:
        """Detect sell signals for a position."""
        if df.empty or len(df) < 20:
            return None

        close = df["close"].values.astype(np.float64)
        high = df["high"].values.astype(np.float64)
        low = df["low"].values.astype(np.float64)
        vol = df["vol"].values.astype(np.float64)

        ema20 = ema(close, 20)
        ema60 = ema(close, 60)
        atr_val = atr(high, low, close, 14)
        adx_val = adx(high, low, close, 14)
        latest_close = close[-1]

        signals = []

        # EMA20 breakdown
        if latest_close < ema20[-1] * 0.97:
            signals.append(SellSignal("EMA20_BREAK", f"跌破EMA20: {((latest_close/ema20[-1]-1)*100):.1f}%", round(float(latest_close), 2), 85))

        # ATR stop
        if len(close) >= 2 and (high[-1] - low[-1]) / close[-2] * 100 > atr_val[-1] * 0.8:
            signals.append(SellSignal("ATR_STOP", f"ATR异常放大: {atr_val[-1]:.2f}", round(float(latest_close), 2), 90))

        # ADX weakening
        if len(adx_val) >= 5 and adx_val[-1] < adx_val[-5] and adx_val[-1] < self.adx_turn:
            signals.append(SellSignal("ADX_WEAK", f"ADX转弱: {adx_val[-1]:.1f}", round(float(latest_close), 2), 75))

        # ETF breakdown
        if etf_df is not None and not etf_df.empty:
            etf_close = etf_df["close"].values.astype(np.float64)
            etf_ema20 = ema(etf_close, 20)
            if etf_close[-1] < etf_ema20[-1]:
                signals.append(SellSignal("ETF_BREAK", "ETF跌破EMA20", round(float(latest_close), 2), 80))

        # ETF Trend Score drop
        if etf_trend_score < self.etf_trend_min:
            signals.append(SellSignal("TREND_DROP", f"ETF趋势分降至{etf_trend_score:.0f}", round(float(latest_close), 2), 85))

        # Volume stagnation (量能持续萎缩 + 价格滞涨)
        if len(close) >= 10:
            ret_5d = (close[-1] / close[-6] - 1) * 100 if len(close) >= 6 else 0
            vol_trend = np.mean(vol[-5:]) / np.maximum(np.mean(vol[-10:-5]), 1)
            if vol_trend < 0.7 and ret_5d < 2:
                signals.append(SellSignal("VOL_STAG", "量缩价平: 量能萎缩+涨幅不足", round(float(latest_close), 2), 70))

        return max(signals, key=lambda x: x.score) if signals else None
