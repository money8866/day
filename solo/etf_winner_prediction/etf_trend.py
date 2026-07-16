#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Step 5: ETF Trend Engine ETF趋势引擎
========================================
对每只ETF计算:
  - Relative Strength (相对强度)
  - Momentum (动量)
  - Acceleration (加速度)
  - Trend Quality (趋势质量)
  - Liquidity (流动性)
  - Drawdown (回撤 - 反向)
  - Volatility (波动率 - 反向)
  - Sharpe
  - Sortino
  - Ulcer Index
  - Rolling Beta
  - Rolling Alpha
  - Maximum Advance
  - Maximum Decline
  - Trend Stability (趋势稳定性)

Output: ETFTrendScore (0-100)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from etf_winner_prediction.indicators import (
    ema, slope, adx, rsi, hurst_exponent,
    relative_strength, breakout_pct, new_high_count,
    volatility, max_drawdown, ulcer_index,
    sharpe_ratio, sortino_ratio, calmar_ratio,
    beta as beta_coef, rolling_corr, natr,
    normalize, percentile_rank, winsorize,
    above_ema_days, volume_ratio,
    returns as compute_returns,
)


@dataclass
class ETFTrendResult:
    etf_code: str = ""
    etf_name: str = ""
    etf_trend_score: float = 0.0
    # 子维度
    relative_strength: float = 0.0
    momentum: float = 0.0
    acceleration: float = 0.0
    trend_quality: float = 0.0
    liquidity: float = 0.0
    drawdown_score: float = 0.0
    volatility_score: float = 0.0
    # 原始指标
    sharpe: float = 0.0
    sortino: float = 0.0
    ulcer: float = 0.0
    rolling_beta: float = 1.0
    rolling_alpha: float = 0.0
    max_advance: float = 0.0
    max_decline: float = 0.0
    trend_stability: float = 0.0
    natr_val: float = 0.0
    reasons: list = field(default_factory=list)


class ETFTrendEngine:
    """ETF趋势引擎 - Step 5"""

    def __init__(self, config: dict):
        self.cfg = config.get("etf_trend", {})
        self.w_rs = self.cfg.get("relative_strength_weight", 0.15)
        self.w_momentum = self.cfg.get("momentum_weight", 0.15)
        self.w_accel = self.cfg.get("acceleration_weight", 0.10)
        self.w_trend = self.cfg.get("trend_quality_weight", 0.15)
        self.w_liq = self.cfg.get("liquidity_weight", 0.10)
        self.w_dd = self.cfg.get("drawdown_weight", 0.10)
        self.w_vol = self.cfg.get("volatility_weight", 0.10)
        self.w_sharpe = self.cfg.get("sharpe_weight", 0.05)
        self.w_sortino = self.cfg.get("sortino_weight", 0.05)
        self.w_ulcer = self.cfg.get("ulcer_weight", 0.05)
        self.rs_period = self.cfg.get("rs_period", 60)
        self.ema_fast = self.cfg.get("ema_fast", 20)
        self.ema_mid = self.cfg.get("ema_mid", 60)
        self.ema_slow = self.cfg.get("ema_slow", 120)
        self.sharpe_period = self.cfg.get("sharpe_period", 60)
        self.ulcer_period = self.cfg.get("ulcer_period", 60)
        self.beta_period = self.cfg.get("beta_period", 60)
        self.min_amount = self.cfg.get("min_amount", 50000000)

    def score(self, etf_data: Dict[str, pd.DataFrame],
              benchmark_close: Optional[np.ndarray] = None) -> Dict[str, ETFTrendResult]:
        """对所有ETF打分"""
        if not etf_data:
            return {}

        metrics_list = []
        for ts_code, df in etf_data.items():
            if df is None or df.empty:
                continue
            if len(df) < max(self.ema_slow, self.rs_period) + 5:
                continue
            try:
                m = self._compute_metrics(ts_code, df, benchmark_close)
                if m:
                    metrics_list.append(m)
            except Exception:
                continue

        if not metrics_list:
            return {}
        return self._assemble_results(metrics_list)

    def _compute_metrics(self, ts_code, df, benchmark) -> Optional[dict]:
        close = np.asarray(df["close"].values, dtype=np.float64)
        high = np.asarray(df["high"].values, dtype=np.float64) if "high" in df.columns else close
        low = np.asarray(df["low"].values, dtype=np.float64) if "low" in df.columns else close
        vol = np.asarray(df["vol"].values, dtype=np.float64) if "vol" in df.columns else np.ones_like(close)
        amount = np.asarray(df["amount"].values, dtype=np.float64) if "amount" in df.columns else vol * close
        n = len(close)

        ema_f = ema(close, self.ema_fast)
        ema_m = ema(close, self.ema_mid)
        ema_s = ema(close, self.ema_slow)

        # RS
        rs_val = 0.0
        if benchmark is not None and len(benchmark) > self.rs_period:
            rs_val = relative_strength(close, benchmark, self.rs_period)

        # 收益率
        ret_20 = float(close[-1] / close[max(-21, -n)] - 1.0) if n >= 21 else 0.0
        ret_60 = float(close[-1] / close[max(-61, -n)] - 1.0) if n >= 61 else 0.0
        ret_20_prev = float(close[max(-21, -n)] / close[max(-41, -n)] - 1.0) if n >= 41 else 0.0
        momentum_accel = ret_20 - ret_20_prev

        # 趋势
        alignment = int(ema_f[-1] > ema_m[-1] > ema_s[-1])
        above_emas = int(close[-1] > ema_f[-1] and close[-1] > ema_m[-1] and close[-1] > ema_s[-1])
        slope_val = slope(close, self.ema_fast)
        h = hurst_exponent(close[-min(n, 240):])
        adx_val = adx(high, low, close, 14)
        persist_days = above_ema_days(close, self.ema_fast)

        # 波动率/回撤
        vol_ann = volatility(close, 20)
        mdd = max_drawdown(close[-min(n, 60):])
        ui = ulcer_index(close, self.ulcer_period)
        natr_val = float(natr(high, low, close, 14)[-1]) if n >= 15 else 0.0
        if not np.isfinite(natr_val):
            natr_val = 0.0

        # 风险调整
        sharpe = sharpe_ratio(close, self.sharpe_period)
        sortino = sortino_ratio(close, self.sharpe_period)
        calmar = calmar_ratio(close, 252)

        # Beta
        beta_val = 1.0
        if benchmark is not None and len(benchmark) > self.beta_period:
            beta_val = beta_coef(close, benchmark, self.beta_period)

        # 趋势稳定性
        trend_stab = self._calc_trend_stability(close, n)

        # 最大涨幅/跌幅
        max_adv = self._calc_max_advance(close, n)
        max_dec = mdd

        # 流动性
        avg_amount = float(np.mean(amount[-20:])) if n >= 20 else 0.0
        vr = volume_ratio(vol, 20)

        # 滚动Alpha
        roll_alpha = 0.0
        if benchmark is not None and len(benchmark) > self.beta_period:
            rets = compute_returns(close)
            bm_rets = compute_returns(benchmark)
            if len(rets) >= self.beta_period and len(bm_rets) >= self.beta_period:
                roll_alpha = float(np.mean(rets[-self.beta_period:]) -
                                   beta_val * np.mean(bm_rets[-self.beta_period:])) * 252

        return {
            "ts_code": ts_code,
            "etf_name": ts_code,
            "n": n, "close": float(close[-1]),
            "rs_val": rs_val, "ret_20": ret_20, "ret_60": ret_60,
            "alignment": alignment, "above_emas": above_emas,
            "slope": float(slope_val), "hurst": float(h),
            "adx": float(adx_val), "persist_days": int(persist_days),
            "momentum_accel": float(momentum_accel),
            "vol": float(vol_ann), "mdd": float(mdd), "ulcer": float(ui),
            "natr": natr_val, "sharpe": float(sharpe), "sortino": float(sortino),
            "calmar": float(calmar), "beta": float(beta_val),
            "trend_stability": float(trend_stab),
            "max_advance": float(max_adv), "max_decline": float(max_dec),
            "roll_alpha": float(roll_alpha),
            "avg_amount": avg_amount, "volume_ratio": float(vr),
        }

    def _calc_trend_stability(self, close, n) -> float:
        if n < 60:
            return 0.5
        ema20 = ema(close, 20)
        above = np.sum(close[-60:] > ema20[-60:])
        return float(above / 60)

    def _calc_max_advance(self, close, n) -> float:
        if n < 20:
            return 0.0
        window = close[-20:]
        running_min = np.minimum.accumulate(window)
        return float((window[-1] - running_min[-1]) / max(running_min[-1], 1e-6))

    def _assemble_results(self, metrics_list: List[dict]) -> Dict[str, ETFTrendResult]:
        n_etf = len(metrics_list)
        if n_etf == 0:
            return {}

        # 提取向量
        rs_v = np.array([m["rs_val"] for m in metrics_list])
        ret20_v = np.array([m["ret_20"] for m in metrics_list])
        ret60_v = np.array([m["ret_60"] for m in metrics_list])
        align_v = np.array([m["alignment"] for m in metrics_list], dtype=float)
        slope_v = np.array([m["slope"] for m in metrics_list])
        hurst_v = np.array([m["hurst"] for m in metrics_list])
        adx_v = np.array([m["adx"] for m in metrics_list])
        persist_v = np.array([m["persist_days"] for m in metrics_list], dtype=float)
        accel_v = np.array([m["momentum_accel"] for m in metrics_list])
        vol_v = np.array([m["vol"] for m in metrics_list])
        mdd_v = np.array([m["mdd"] for m in metrics_list])
        ulcer_v = np.array([m["ulcer"] for m in metrics_list])
        natr_v = np.array([m["natr"] for m in metrics_list])
        sharpe_v = np.array([m["sharpe"] for m in metrics_list])
        sortino_v = np.array([m["sortino"] for m in metrics_list])
        amt_v = np.array([m["avg_amount"] for m in metrics_list])
        beta_v = np.array([m["beta"] for m in metrics_list])
        stab_v = np.array([m["trend_stability"] for m in metrics_list])
        adv_v = np.array([m["max_advance"] for m in metrics_list])
        alpha_v = np.array([m["roll_alpha"] for m in metrics_list])

        # 1. Relative Strength
        rs_score = (percentile_rank(winsorize(rs_v)) * 50 +
                    percentile_rank(winsorize(ret20_v)) * 25 +
                    percentile_rank(winsorize(ret60_v)) * 25) * 100
        rs_score = np.clip(rs_score, 0, 100)

        # 2. Momentum
        mom_score = (percentile_rank(winsorize(ret20_v)) * 40 +
                     percentile_rank(winsorize(ret60_v)) * 30 +
                     percentile_rank(winsorize(accel_v)) * 30) * 100
        mom_score = np.clip(mom_score, 0, 100)

        # 3. Acceleration
        accel_score = (percentile_rank(winsorize(accel_v)) * 50 +
                       percentile_rank(winsorize(adv_v)) * 30 +
                       percentile_rank(winsorize(alpha_v)) * 20) * 100
        accel_score = np.clip(accel_score, 0, 100)

        # 4. Trend Quality
        trend_score = (align_v * 30 +
                       np.clip(winsorize(slope_v) * 2000, 0, 20) +
                       np.clip((hurst_v - 0.5) * 200, 0, 25) +
                       np.clip(adx_v / 50 * 100, 0, 25))
        trend_score = np.clip(trend_score, 0, 100)

        # 5. Liquidity
        amt_norm = np.clip(amt_v / 5e8 * 100, 0, 60)
        liq_score = np.clip(amt_norm, 0, 100)

        # 6. Drawdown (反向)
        dd_score = np.clip(100.0 - percentile_rank(winsorize(mdd_v)) * 100, 0, 100)

        # 7. Volatility (反向)
        vol_score = np.clip(100.0 - percentile_rank(winsorize(vol_v)) * 100, 0, 100)
        natr_score = np.clip(100.0 - natr_v * 10, 0, 100)
        vol_score = vol_score * 0.6 + natr_score * 0.4

        # 最终
        final = (
            rs_score * self.w_rs +
            mom_score * self.w_momentum +
            accel_score * self.w_accel +
            trend_score * self.w_trend +
            liq_score * self.w_liq +
            dd_score * self.w_dd +
            vol_score * self.w_vol +
            percentile_rank(winsorize(sharpe_v)) * 100 * self.w_sharpe +
            percentile_rank(winsorize(sortino_v)) * 100 * self.w_sortino +
            (100.0 - percentile_rank(winsorize(ulcer_v)) * 100) * self.w_ulcer
        )
        final = np.clip(final, 0, 100)

        results = {}
        for i, m in enumerate(metrics_list):
            ts_code = m["ts_code"]
            r = ETFTrendResult(
                etf_code=ts_code,
                etf_trend_score=round(float(final[i]), 2),
                relative_strength=round(float(rs_score[i]), 2),
                momentum=round(float(mom_score[i]), 2),
                acceleration=round(float(accel_score[i]), 2),
                trend_quality=round(float(trend_score[i]), 2),
                liquidity=round(float(liq_score[i]), 2),
                drawdown_score=round(float(dd_score[i]), 2),
                volatility_score=round(float(vol_score[i]), 2),
                sharpe=round(float(sharpe_v[i]), 3),
                sortino=round(float(sortino_v[i]), 3),
                ulcer=round(float(ulcer_v[i]), 3),
                rolling_beta=round(float(beta_v[i]), 3),
                rolling_alpha=round(float(alpha_v[i]), 4),
                max_advance=round(float(adv_v[i]), 4),
                max_decline=round(float(mdd_v[i]), 4),
                trend_stability=round(float(stab_v[i]), 3),
                natr_val=round(float(natr_v[i]), 2),
            )
            r.reasons = self._build_reasons(r, m)
            results[ts_code] = r
        return results

    def _build_reasons(self, r: ETFTrendResult, m: dict) -> list:
        parts = []
        if r.relative_strength >= 70:
            parts.append("RS强")
        if r.trend_quality >= 70:
            parts.append("趋势优")
        if r.momentum >= 70:
            parts.append("动量强")
        if r.acceleration >= 70:
            parts.append("加速上行")
        if r.liquidity >= 70:
            parts.append("流动性佳")
        if r.drawdown_score >= 70:
            parts.append("回撤低")
        if r.sharpe >= 1.0:
            parts.append(f"Sharpe={r.sharpe:.2f}")
        if r.trend_stability >= 0.7:
            parts.append("趋势稳定")
        return parts or ["中性"]