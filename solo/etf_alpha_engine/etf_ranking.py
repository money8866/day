#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Module 4: ETF Ranking Engine ETF排名引擎
================================================
For every ETF calculate:
  - Relative Strength (RS)
  - Trend Quality
  - Momentum Quality
  - Liquidity
  - Tracking Stability
  - Volatility (lower better)
  - Drawdown (lower better)
  - Acceleration
  - RS Rank
  - Sharpe
  - Sortino
  - Ulcer Index
  - Rolling Beta

Output:
  - ETF Alpha Score (0-100)
  - Expected Return
  - Expected Holding Days
  - Confidence
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from etf_alpha_engine.indicators import (
    ema, sma, slope, atr, natr, adx, rsi, hurst_exponent,
    relative_strength, breakout_pct, new_high_count,
    volatility, max_drawdown, ulcer_index,
    sharpe_ratio, sortino_ratio, calmar_ratio,
    beta as beta_coef, rolling_corr,
    normalize, percentile_rank, winsorize,
    above_ema_days, volume_ratio,
)


@dataclass
class ETFRankingResult:
    """ETF排名结果"""
    etf_code: str = ""
    etf_name: str = ""
    theme: str = ""
    etf_alpha_score: float = 0.0
    # 子维度（0-100）
    relative_strength: float = 0.0
    trend_quality: float = 0.0
    momentum_quality: float = 0.0
    liquidity: float = 0.0
    tracking_stability: float = 0.0
    volatility_score: float = 0.0      # 反向（越低波动分越高）
    drawdown_score: float = 0.0        # 反向
    acceleration: float = 0.0
    # 原始指标
    rs_rank: int = 0
    sharpe: float = 0.0
    sortino: float = 0.0
    ulcer: float = 0.0
    rolling_beta: float = 1.0
    natr_val: float = 0.0
    # 输出
    expected_return: float = 0.0
    expected_holding_days: int = 0
    confidence: float = 0.0
    reasons: list = field(default_factory=list)


class ETFRankingEngine:
    """ETF排名引擎

    独立可运行，输出每只ETF的0-100 Alpha分数。
    所有子维度独立计算、可复用、可参数优化。
    """

    def __init__(self, config: dict):
        self.cfg = config.get("etf_ranking", {})
        self.w_rs = self.cfg.get("relative_strength_weight", 0.20)
        self.w_trend = self.cfg.get("trend_quality_weight", 0.20)
        self.w_mom = self.cfg.get("momentum_quality_weight", 0.15)
        self.w_liq = self.cfg.get("liquidity_weight", 0.10)
        self.w_track = self.cfg.get("tracking_stability_weight", 0.10)
        self.w_vol = self.cfg.get("volatility_weight", 0.10)
        self.w_dd = self.cfg.get("drawdown_weight", 0.05)
        self.w_accel = self.cfg.get("acceleration_weight", 0.10)
        self.rs_period = self.cfg.get("rs_period", 60)
        self.ema_fast = self.cfg.get("ema_fast", 20)
        self.ema_mid = self.cfg.get("ema_mid", 60)
        self.ema_slow = self.cfg.get("ema_slow", 120)
        self.sharpe_period = self.cfg.get("sharpe_period", 60)
        self.ulcer_period = self.cfg.get("ulcer_period", 60)
        self.beta_period = self.cfg.get("beta_period", 60)
        self.min_amount = self.cfg.get("min_amount", 50000000)
        self.etf_themes = config.get("etf_universe", {})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def score(self,
              etf_data: Dict[str, pd.DataFrame],
              benchmark_close: Optional[np.ndarray] = None,
              ) -> Dict[str, ETFRankingResult]:
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
            except Exception as exc:
                continue

        if not metrics_list:
            return {}

        # 横截面排名
        results = self._assemble_results(metrics_list)
        return results

    # ------------------------------------------------------------------
    # 指标计算
    # ------------------------------------------------------------------
    def _compute_metrics(self, ts_code: str, df: pd.DataFrame,
                         benchmark: Optional[np.ndarray]) -> Optional[dict]:
        close = np.asarray(df["close"].values, dtype=np.float64)
        high = np.asarray(df["high"].values, dtype=np.float64) if "high" in df.columns else close
        low = np.asarray(df["low"].values, dtype=np.float64) if "low" in df.columns else close
        vol = np.asarray(df["vol"].values, dtype=np.float64) if "vol" in df.columns else np.ones_like(close)
        amount = np.asarray(df["amount"].values, dtype=np.float64) if "amount" in df.columns else vol * close
        n = len(close)

        ema_f = ema(close, self.ema_fast)
        ema_m = ema(close, self.ema_mid)
        ema_s = ema(close, self.ema_slow)

        # RS vs benchmark
        rs_val = 0.0
        if benchmark is not None and len(benchmark) > self.rs_period:
            rs_val = relative_strength(close, benchmark, self.rs_period)

        # 收益率
        ret_20 = float(close[-1] / close[max(-21, -n)] - 1.0) if n >= 21 else 0.0
        ret_60 = float(close[-1] / close[max(-61, -n)] - 1.0) if n >= 61 else 0.0

        # 动量
        roc_20 = ret_20
        roc_60 = ret_60
        roc_20_prev = float(close[max(-21, -n)] / close[max(-41, -n)] - 1.0) if n >= 41 else 0.0
        momentum_accel = roc_20 - roc_20_prev

        # 趋势
        alignment = int(ema_f[-1] > ema_m[-1] > ema_s[-1])
        above_emas = int(close[-1] > ema_f[-1] and close[-1] > ema_m[-1] and close[-1] > ema_s[-1])
        slope_val = slope(close, self.ema_fast)
        h = hurst_exponent(close[-min(n, 240):])
        adx_val = adx(high, low, close, 14)

        # 持续性
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

        # 突破
        pct_60 = breakout_pct(close, high, 60)
        pct_120 = breakout_pct(close, high, 120)
        new_highs = new_high_count(close, 20)

        # 流动性
        avg_amount = float(np.mean(amount[-20:])) if n >= 20 else 0.0
        vr = volume_ratio(vol, 20)

        return {
            "ts_code": ts_code,
            "etf_name": str(df.get("etf_name", pd.Series([ts_code])).iloc[0]) if hasattr(df.get("etf_name", None), "iloc") else ts_code,
            "n": n,
            "close": float(close[-1]),
            # RS
            "rs_val": rs_val,
            "ret_20": ret_20,
            "ret_60": ret_60,
            # 趋势
            "alignment": alignment,
            "above_emas": above_emas,
            "slope": float(slope_val),
            "hurst": float(h),
            "adx": float(adx_val),
            "persist_days": int(persist_days),
            # 动量
            "roc_20": roc_20,
            "roc_60": roc_60,
            "momentum_accel": float(momentum_accel),
            # 风险
            "vol": float(vol_ann),
            "mdd": float(mdd),
            "ulcer": float(ui),
            "natr": natr_val,
            "sharpe": float(sharpe),
            "sortino": float(sortino),
            "calmar": float(calmar),
            "beta": float(beta_val),
            # 突破
            "pct_60": float(pct_60),
            "pct_120": float(pct_120),
            "new_highs": int(new_highs),
            # 流动性
            "avg_amount": avg_amount,
            "volume_ratio": float(vr),
        }

    # ------------------------------------------------------------------
    # 横截面排名 + 组装结果
    # ------------------------------------------------------------------
    def _assemble_results(self, metrics_list: List[dict]) -> Dict[str, ETFRankingResult]:
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
        calmar_v = np.array([m["calmar"] for m in metrics_list])
        beta_v = np.array([m["beta"] for m in metrics_list])
        pct60_v = np.array([m["pct_60"] for m in metrics_list])
        pct120_v = np.array([m["pct_120"] for m in metrics_list])
        nh_v = np.array([m["new_highs"] for m in metrics_list], dtype=float)
        amt_v = np.array([m["avg_amount"] for m in metrics_list])
        vr_v = np.array([m["volume_ratio"] for m in metrics_list])

        # 子分数（0-100）
        # 1. Relative Strength
        rs_score = percentile_rank(winsorize(rs_v)) * 50 + \
                   percentile_rank(winsorize(ret20_v)) * 25 + \
                   percentile_rank(winsorize(ret60_v)) * 25
        rs_score = np.clip(rs_score * 100, 0, 100)

        # 2. Trend Quality
        trend_score = (align_v * 30 +
                       np.clip(winsorize(slope_v) * 2000, 0, 20) +
                       np.clip((hurst_v - 0.5) * 200, 0, 25) +
                       np.clip(adx_v / 50 * 100, 0, 25))
        trend_score = np.clip(trend_score, 0, 100)

        # 3. Momentum Quality
        mom_score = (percentile_rank(winsorize(ret20_v)) * 40 +
                     percentile_rank(winsorize(ret60_v)) * 30 +
                     percentile_rank(winsorize(accel_v)) * 30) * 100
        mom_score = np.clip(mom_score, 0, 100)

        # 4. Liquidity
        amt_norm = np.clip(amt_v / 5e8 * 100, 0, 60)  # 5亿=60分
        vr_score = np.clip(vr_v * 30, 0, 40)
        liq_score = amt_norm + vr_score
        liq_score = np.clip(liq_score, 0, 100)

        # 5. Tracking Stability (用Sharpe+Sortino+Calmar)
        track_score = (percentile_rank(winsorize(sharpe_v)) * 40 +
                       percentile_rank(winsorize(sortino_v)) * 35 +
                       percentile_rank(winsorize(calmar_v)) * 25) * 100
        track_score = np.clip(track_score, 0, 100)

        # 6. Volatility (反向)
        vol_score = np.clip(100.0 - percentile_rank(winsorize(vol_v)) * 100, 0, 100)
        # 用NATR补充
        natr_score = np.clip(100.0 - natr_v * 10, 0, 100)
        vol_score = vol_score * 0.6 + natr_score * 0.4

        # 7. Drawdown (反向)
        dd_score = np.clip(100.0 - percentile_rank(winsorize(mdd_v)) * 100, 0, 100)

        # 8. Acceleration
        accel_score = (percentile_rank(winsorize(accel_v)) * 50 +
                       percentile_rank(winsorize(nh_v)) * 30 +
                       percentile_rank(winsorize(-pct60_v)) * 20) * 100
        accel_score = np.clip(accel_score, 0, 100)

        # 最终Alpha分数
        final = (
            rs_score * self.w_rs +
            trend_score * self.w_trend +
            mom_score * self.w_mom +
            liq_score * self.w_liq +
            track_score * self.w_track +
            vol_score * self.w_vol +
            dd_score * self.w_dd +
            accel_score * self.w_accel
        )
        final = np.clip(final, 0, 100)

        # RS排名
        rs_order = np.argsort(rs_v)[::-1]
        rs_rank = np.zeros(n_etf, dtype=int)
        for rank, idx in enumerate(rs_order):
            rs_rank[idx] = rank + 1

        results = {}
        for i, m in enumerate(metrics_list):
            ts_code = m["ts_code"]
            r = ETFRankingResult(
                etf_code=ts_code,
                etf_name=m.get("etf_name", ts_code),
                theme=self.etf_themes.get(ts_code, ""),
                etf_alpha_score=round(float(final[i]), 2),
                relative_strength=round(float(rs_score[i]), 2),
                trend_quality=round(float(trend_score[i]), 2),
                momentum_quality=round(float(mom_score[i]), 2),
                liquidity=round(float(liq_score[i]), 2),
                tracking_stability=round(float(track_score[i]), 2),
                volatility_score=round(float(vol_score[i]), 2),
                drawdown_score=round(float(dd_score[i]), 2),
                acceleration=round(float(accel_score[i]), 2),
                rs_rank=int(rs_rank[i]),
                sharpe=round(float(sharpe_v[i]), 3),
                sortino=round(float(sortino_v[i]), 3),
                ulcer=round(float(ulcer_v[i]), 3),
                rolling_beta=round(float(beta_v[i]), 3),
                natr_val=round(float(natr_v[i]), 2),
            )
            # 估计预期收益和持有天数
            r.expected_return = self._estimate_expected_return(m, float(final[i]))
            r.expected_holding_days = self._estimate_holding_days(m, float(final[i]))
            r.confidence = self._estimate_confidence(m, float(final[i]))
            r.reasons = self._build_reasons(r, m)
            results[ts_code] = r
        return results

    # ------------------------------------------------------------------
    # 估计预期收益
    # ------------------------------------------------------------------
    def _estimate_expected_return(self, m: dict, alpha_score: float) -> float:
        # 基于动量+趋势持续性估计未来20-60天预期收益
        base = m["ret_20"] * 0.4 + m["ret_60"] * 0.3
        # 趋势强 -> 提升预期
        trend_bonus = (m["alignment"] * 0.05 + (m["hurst"] - 0.5) * 0.1)
        # Alpha分加成
        alpha_bonus = (alpha_score - 50) / 50 * 0.05
        expected = base + trend_bonus + alpha_bonus
        return float(np.clip(expected, -0.2, 0.5))

    # ------------------------------------------------------------------
    # 估计预期持有天数
    # ------------------------------------------------------------------
    def _estimate_holding_days(self, m: dict, alpha_score: float) -> int:
        # 趋势持续性高 -> 持有更久
        persist = m["persist_days"]
        hurst = m["hurst"]
        adx = m["adx"]
        base = 20  # 最小持有20天
        base += min(persist, 20)
        if hurst > 0.6:
            base += 10
        if adx > 30:
            base += 5
        if alpha_score > 85:
            base += 5
        return int(np.clip(base, 20, 60))

    # ------------------------------------------------------------------
    # 估计置信度
    # ------------------------------------------------------------------
    def _estimate_confidence(self, m: dict, alpha_score: float) -> float:
        # 多维度一致性 -> 高置信度
        align = m["alignment"]
        hurst = m["hurst"]
        adx = m["adx"]
        sharpe = m["sharpe"]
        conf = 50.0
        if align:
            conf += 15
        if hurst > 0.55:
            conf += 10
        if adx > 25:
            conf += 10
        if sharpe > 1.0:
            conf += 10
        conf += (alpha_score - 50) * 0.1
        return float(np.clip(conf, 0, 100))

    def _build_reasons(self, r: ETFRankingResult, m: dict) -> list:
        parts = []
        if r.relative_strength >= 70:
            parts.append(f"RS强(排名{r.rs_rank})")
        if r.trend_quality >= 70:
            parts.append("趋势优(多头排列)")
        if r.momentum_quality >= 70:
            parts.append("动量强")
        if r.liquidity >= 70:
            parts.append("流动性佳")
        if r.tracking_stability >= 70:
            parts.append(f"Sharpe={r.sharpe:.2f}")
        if r.volatility_score >= 70:
            parts.append("波动低")
        if r.acceleration >= 70:
            parts.append("加速上行")
        if r.expected_return >= 0.10:
            parts.append(f"预期收益{r.expected_return*100:.1f}%")
        return parts or ["中性"]
