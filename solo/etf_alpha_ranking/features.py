#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Feature Engine
==============
Generates the per-ETF feature dataset. Each ETF produces 100+ features.

Modules:
  A. ETF Trend Features      (returns, MA, MACD, breakout, new highs)
  B. Relative Strength       (alpha vs benchmark, RS rank)
  C. Risk Features           (vol, ATR, drawdown, Sharpe, Sortino, Ulcer)
  D. Theme Persistence       (from theme_persistence.py)
  E. Leader Persistence      (from leader_persistence.py)
  F. Breadth Features        (from breadth.py)
  G. Market Regime           (from market_regime.py)

The feature dict for each ETF is flat (one-level keys) so it can be
loaded directly into a LightGBM training matrix.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import indicators as ind
from .theme_persistence import ThemePersistenceResult
from .leader_persistence import LeaderResult
from .breadth import BreadthResult
from .market_regime import MarketRegimeResult

LOG = logging.getLogger("etf_alpha_ranking.features")


class FeatureEngine:
    """Build a 100+ feature vector per ETF."""

    def __init__(self, config: dict):
        self.config = config
        etf_cfg = config.get("etf_trend", {})
        self.return_periods = etf_cfg.get("return_periods", [5, 10, 20, 40, 60])
        self.ma_short = etf_cfg.get("ma_short", 20)
        self.ma_long = etf_cfg.get("ma_long", 60)
        self.ema_fast = etf_cfg.get("ema_short", 12)
        self.ema_slow = etf_cfg.get("ema_long", 26)
        self.macd_signal = etf_cfg.get("macd_signal", 9)
        self.breakout_period = etf_cfg.get("breakout_period", 60)
        self.new_high_period = etf_cfg.get("new_high_period", 60)
        risk_cfg = config.get("risk", {})
        self.vol_short = risk_cfg.get("vol_short", 20)
        self.vol_long = risk_cfg.get("vol_long", 60)
        self.atr_period = risk_cfg.get("atr_period", 14)
        self.dd_period = risk_cfg.get("drawdown_period", 60)
        self.sharpe_periods = risk_cfg.get("sharpe_period", [20, 60])
        self.sortino_period = risk_cfg.get("sortino_period", 60)
        self.ulcer_period = risk_cfg.get("ulcer_period", 60)
        self.alpha_periods = config.get("relative_strength", {}).get(
            "alpha_periods", [20, 40, 60])
        self.rf = config.get("general", {}).get("risk_free_rate", 0.03)

    # ------------------------------------------------------------------
    # Module A: ETF Trend Features
    # ------------------------------------------------------------------
    def _module_a(self, close, high, low, vol, amount, pct_chg) -> Dict[str, float]:
        f: Dict[str, float] = {}
        n = len(close)
        if n < 30:
            return f
        # Returns
        f["ret_1d"] = float(pct_chg[-1] / 100.0) if len(pct_chg) else 0.0
        for p in self.return_periods:
            if n > p:
                f[f"ret_{p}d"] = float(close[-1] / close[-p - 1] - 1.0)
            else:
                f[f"ret_{p}d"] = 0.0
        # MA distances
        ma20 = ind.sma(close, self.ma_short)
        ma60 = ind.sma(close, self.ma_long)
        f["close_ma20"] = float((close[-1] - ma20[-1]) / (abs(ma20[-1]) + 1e-9)) if np.isfinite(ma20[-1]) else 0.0
        f["close_ma60"] = float((close[-1] - ma60[-1]) / (abs(ma60[-1]) + 1e-9)) if np.isfinite(ma60[-1]) else 0.0
        f["ma20_slope"] = float(ind.slope(ma20, 10))
        f["ma60_slope"] = float(ind.slope(ma60, 20))
        # EMA trend alignment
        ema_fast = ind.ema(close, self.ema_fast)
        ema_slow = ind.ema(close, self.ema_slow)
        f["ema_alignment"] = float(
            (ema_fast[-1] > ema_slow[-1]) * 1.0
            + (close[-1] > ema_fast[-1]) * 1.0) if n > self.ema_slow else 0.0
        f["ema_spread"] = float((ema_fast[-1] - ema_slow[-1]) / (abs(ema_slow[-1]) + 1e-9)) if n > self.ema_slow else 0.0
        # MACD
        if n > self.ema_slow + self.macd_signal:
            macd_line, signal_line, hist = ind.macd(close, self.ema_fast, self.ema_slow, self.macd_signal)
            f["macd_hist"] = float(hist[-1])
            f["macd_above_signal"] = float(macd_line[-1] > signal_line[-1])
            f["macd_hist_slope"] = float(ind.slope(hist, 5))
        else:
            f["macd_hist"] = 0.0
            f["macd_above_signal"] = 0.0
            f["macd_hist_slope"] = 0.0
        # Breakout distance & new highs
        f["breakout_dist"] = float(ind.breakout_pct(close, high, self.breakout_period) / 100.0)
        f["new_high_count_60"] = float(ind.new_high_count(high, self.new_high_period))
        f["price_position_60"] = float(ind.price_position(close, high, low, self.new_high_period))
        # Momentum acceleration
        if n > 40:
            f["momentum_accel"] = float(f.get("ret_20d", 0.0) - (close[-21] / close[-41] - 1.0))
        else:
            f["momentum_accel"] = 0.0
        # Volume / liquidity
        f["vol_ratio"] = float(ind.volume_ratio(vol, 20))
        f["amt_5d_avg"] = float(np.mean(amount[-5:])) if n > 5 else 0.0
        f["amt_20d_avg"] = float(np.mean(amount[-20:])) if n > 20 else 0.0
        f["amt_trend"] = float(f["amt_5d_avg"] / (f["amt_20d_avg"] + 1e-9))
        if n > 20:
            f["amt_growth"] = float((np.mean(amount[-5:]) - np.mean(amount[-20:-5]))
                                    / (np.mean(amount[-20:-5]) + 1e-9))
        else:
            f["amt_growth"] = 0.0
        # Trend persistence & quality
        f["up_ratio_20d"] = float(np.sum(pct_chg[-20:] > 0) / 20.0) if len(pct_chg) >= 20 else 0.5
        f["consec_up"] = float(ind.consecutive_up_days(pct_chg))
        f["above_ema20_days"] = float(ind.above_ema_days(close, 20))
        # Vol-price correlation (capital flow confirmation)
        if n >= 20 and len(pct_chg) >= 20:
            v20 = vol[-20:].astype(float)
            p20 = pct_chg[-20:].astype(float)
            if np.std(v20) > 1e-6 and np.std(p20) > 1e-6:
                f["vol_price_corr_20d"] = float(np.corrcoef(v20, p20)[0, 1])
            else:
                f["vol_price_corr_20d"] = 0.0
        else:
            f["vol_price_corr_20d"] = 0.0
        # RSI / ADX / Hurst
        f["rsi_14"] = float(ind.rsi(close, 14))
        f["adx_14"] = float(ind.adx(high, low, close, 14))
        f["hurst"] = float(ind.hurst_exponent(close[-min(n, 240):]))
        # KDJ
        k, d, j = ind.kdj(high, low, close, 9)
        f["kdj_k"], f["kdj_d"], f["kdj_j"] = k, d, j
        # Interaction features
        f["ma_spread_norm"] = float((ma20[-1] - ma60[-1]) / (abs(ma60[-1]) + 1e-9)) if np.isfinite(ma60[-1]) else 0.0
        f["recovery_power"] = float(f.get("ret_20d", 0.0) * f["up_ratio_20d"])
        f["vol_price_momentum"] = float(f["vol_price_corr_20d"] * max(f.get("ret_20d", 0.0), 0.0))
        f["momentum_reversal"] = float(f.get("ret_20d", 0.0) - f.get("ret_60d", 0.0))
        f["natr_14"] = float(ind.natr(high, low, close, self.atr_period)[-1]) if n > self.atr_period else 0.0
        return f

    # ------------------------------------------------------------------
    # Module B: Relative Strength
    # ------------------------------------------------------------------
    def _module_b(self, close, bench_close) -> Dict[str, float]:
        f: Dict[str, float] = {}
        n = len(close)
        for p in self.alpha_periods:
            if n > p and len(bench_close) > p:
                etf_ret = close[-1] / close[-p - 1] - 1.0
                b_ret = bench_close[-1] / bench_close[-p - 1] - 1.0
                f[f"alpha{p}"] = float(etf_ret - b_ret)
            else:
                f[f"alpha{p}"] = 0.0
        # RS rank filled later (cross-sectional) -> placeholder, keep raw RS ratio
        f["rs_ratio"] = float(ind.relative_strength(close, bench_close))
        f["beta_60"] = float(ind.beta(close, bench_close, 60))
        f["corr_bench_60"] = float(ind.rolling_corr(close, bench_close, 60))
        return f

    # ------------------------------------------------------------------
    # Module C: Risk Features
    # ------------------------------------------------------------------
    def _module_c(self, close, high, low) -> Dict[str, float]:
        f: Dict[str, float] = {}
        n = len(close)
        f["vol_20d"] = float(ind.volatility(close, self.vol_short))
        f["vol_60d"] = float(ind.volatility(close, self.vol_long)) if n > self.vol_long else f["vol_20d"]
        f["vol_ratio"] = float(f["vol_20d"] / (f["vol_60d"] + 1e-9))
        f["atr_14"] = float(ind.atr(high, low, close, self.atr_period)[-1]) if n > self.atr_period else 0.0
        f["max_dd_60"] = float(ind.max_drawdown(close[-self.dd_period:])) if n > self.dd_period else 0.0
        f["max_dd_20"] = float(ind.max_drawdown(close[-20:])) if n > 20 else 0.0
        f["ulcer_60"] = float(ind.ulcer_index(close, self.ulcer_period)) if n > self.ulcer_period else 0.0
        for p in self.sharpe_periods:
            f[f"sharpe_{p}"] = float(ind.sharpe_ratio(close, p, self.rf)) if n > p else 0.0
        f["sortino_60"] = float(ind.sortino_ratio(close, self.sortino_period, self.rf)) if n > self.sortino_period else 0.0
        f["calmar_252"] = float(ind.calmar_ratio(close, 252)) if n > 60 else 0.0
        return f

    # ------------------------------------------------------------------
    # Modules D/E/F/G integration
    # ------------------------------------------------------------------
    def _module_integrations(self, theme_r: Optional[ThemePersistenceResult],
                            leader_r: Optional[LeaderResult],
                            breadth_r: Optional[BreadthResult],
                            market_r: Optional[MarketRegimeResult]) -> Dict[str, float]:
        f: Dict[str, float] = {}
        if theme_r:
            f["theme_persistence"] = theme_r.theme_persistence
            f["theme_rank"] = float(theme_r.theme_rank)
            f["theme_breadth"] = theme_r.breadth
            f["theme_trend_stability"] = theme_r.trend_stability
            f["theme_breadth_expansion"] = theme_r.breadth_expansion
            f["theme_leader_persistence"] = theme_r.leader_persistence
            f["theme_capital_consistency"] = theme_r.capital_consistency
            f["theme_catalyst_duration"] = theme_r.catalyst_duration
            f["theme_crowding_penalty"] = theme_r.crowding_penalty
            f["theme_expected_duration"] = theme_r.expected_duration
            f["theme_rotation_probability"] = theme_r.rotation_probability
        if leader_r:
            f["leader_score"] = leader_r.leader_score
            f["leader_RS"] = leader_r.leader_RS
            f["leader_return20"] = leader_r.leader_return20
            f["leader_return60"] = leader_r.leader_return60
            f["leader_breakout"] = leader_r.leader_breakout
            f["leader_health"] = leader_r.leader_health
            f["leader_persistence"] = leader_r.leader_persistence
        if breadth_r:
            f["breadth_above_ma20"] = breadth_r.above_MA20_ratio
            f["breadth_above_ma60"] = breadth_r.above_MA60_ratio
            f["breadth_strong_ratio"] = breadth_r.strong_stock_ratio
            f["breadth_rs_top30"] = breadth_r.RS_top30_ratio
            f["breadth_limit_up"] = float(breadth_r.limit_up_count)
            f["breadth_acceleration"] = breadth_r.breadth_acceleration
            f["breadth_stock_count"] = float(breadth_r.theme_stock_count)
        if market_r:
            f["market_score"] = market_r.market_score
            f["market_csi300_trend"] = market_r.csi300_trend
            f["market_breadth"] = market_r.market_breadth
            f["market_turnover"] = market_r.market_turnover
            f["market_volatility"] = market_r.market_volatility
            f["market_exposure"] = market_r.recommended_exposure
        # Interaction features
        tp = f.get("theme_persistence", 0.0)
        ls = f.get("leader_score", 0.0)
        et = f.get("ret_20d", 0.0)
        ms = f.get("market_score", 50.0)
        f["theme_leader_product"] = tp * ls / 100.0
        f["theme_market_align"] = tp * ms / 100.0
        f["leader_trend_product"] = ls * (et * 100.0 + 50.0) / 100.0
        return f

    # ------------------------------------------------------------------
    # Public: build features for one ETF
    # ------------------------------------------------------------------
    def build(self, etf_code: str, df: pd.DataFrame, bench_close: np.ndarray,
              theme_r: Optional[ThemePersistenceResult] = None,
              leader_r: Optional[LeaderResult] = None,
              breadth_r: Optional[BreadthResult] = None,
              market_r: Optional[MarketRegimeResult] = None) -> Dict[str, float]:
        if df is None or df.empty:
            return {}
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float) if "high" in df.columns else close
        low = df["low"].values.astype(float) if "low" in df.columns else close
        vol = df["vol"].values.astype(float) if "vol" in df.columns else np.ones_like(close)
        amount = df["amount"].values.astype(float) if "amount" in df.columns else np.zeros_like(close)
        pct = df["pct_chg"].values.astype(float) if "pct_chg" in df.columns else np.zeros_like(close)

        feats: Dict[str, float] = {}
        feats.update(self._module_a(close, high, low, vol, amount, pct))
        feats.update(self._module_b(close, bench_close))
        feats.update(self._module_c(close, high, low))
        feats.update(self._module_integrations(theme_r, leader_r, breadth_r, market_r))
        feats["etf_code"] = 0.0  # placeholder, set by caller
        return feats

    # ------------------------------------------------------------------
    # Cross-sectional RS rank (Module B continuation)
    # ------------------------------------------------------------------
    @staticmethod
    def add_cross_sectional(df: pd.DataFrame, feature_cols: List[str]) -> pd.DataFrame:
        """Add percentile-rank cross-sectional features for the given columns.

        New columns: <col>_rank (0-100) computed within each date group.
        """
        for col in feature_cols:
            rank_col = f"{col}_rank"
            if col in df.columns:
                df[rank_col] = df.groupby("date")[col].transform(
                    lambda s: pd.Series(ind.percentile_rank(s.values), index=s.index))
        return df
