#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Module 6: Risk Engine 风险引擎
================================================
Estimate:
  - Expected Drawdown
  - Volatility
  - Failure Probability
  - Rotation Risk
  - Concentration Risk
  - Market Correlation

Output:
  - Risk Score (0-100, higher = more risky)
  - Suggested Position
  - Stop Loss
  - Take Profit
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np
import pandas as pd

from etf_alpha_engine.indicators import (
    atr, natr, volatility, max_drawdown, ulcer_index,
    beta as beta_coef, rolling_corr,
)
from etf_alpha_engine.indicators import returns as compute_returns


@dataclass
class RiskResult:
    """风险引擎结果"""
    etf_code: str = ""
    risk_score: float = 50.0       # 0-100, higher = more risky
    # 子维度（higher = more risky）
    expected_drawdown: float = 0.0
    volatility_risk: float = 0.0
    failure_probability: float = 0.0
    rotation_risk: float = 0.0
    concentration_risk: float = 0.0
    market_correlation: float = 0.0
    # 原始指标
    natr_val: float = 0.0
    max_dd: float = 0.0
    ulcer: float = 0.0
    beta: float = 1.0
    vol_annual: float = 0.0
    # 输出
    suggested_position: float = 0.5
    stop_loss: float = 0.0          # 止损价（百分比）
    take_profit: float = 0.0        # 止盈价（百分比）
    reasons: list = field(default_factory=list)


class RiskEngine:
    """风险引擎

    独立可运行，输出每只ETF的风险分数和仓位建议。
    所有子维度独立计算、可复用、可参数优化。
    """

    def __init__(self, config: dict):
        self.cfg = config.get("risk_engine", {})
        self.w_dd = self.cfg.get("expected_drawdown_weight", 0.25)
        self.w_vol = self.cfg.get("volatility_weight", 0.20)
        self.w_fail = self.cfg.get("failure_probability_weight", 0.15)
        self.w_rot = self.cfg.get("rotation_risk_weight", 0.15)
        self.w_conc = self.cfg.get("concentration_risk_weight", 0.10)
        self.w_corr = self.cfg.get("market_correlation_weight", 0.15)
        self.atr_period = self.cfg.get("atr_period", 14)
        self.dd_lookback = self.cfg.get("drawdown_lookback", 60)
        self.vol_period = self.cfg.get("vol_period", 20)
        self.position_map = self.cfg.get("position_by_risk", {
            "low": 1.0, "medium": 0.6, "high": 0.3, "extreme": 0.0
        })
        self.stop_atr_mult = self.cfg.get("stop_loss_atr_multiple", 2.0)
        self.tp_atr_mult = self.cfg.get("take_profit_atr_multiple", 6.0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def score(self,
              etf_data: Dict[str, pd.DataFrame],
              benchmark_close: Optional[np.ndarray] = None,
              ) -> Dict[str, RiskResult]:
        """对所有ETF计算风险"""
        results = {}
        for code, df in etf_data.items():
            if df is None or df.empty or len(df) < 30:
                continue
            r = self._score_one(code, df, benchmark_close)
            results[code] = r
        return results

    def _score_one(self, code, df, benchmark) -> RiskResult:
        r = RiskResult(etf_code=code)
        close = np.asarray(df["close"].values, dtype=np.float64)
        high = np.asarray(df["high"].values, dtype=np.float64) if "high" in df.columns else close
        low = np.asarray(df["low"].values, dtype=np.float64) if "low" in df.columns else close
        n = len(close)

        # ① Expected Drawdown
        mdd = max_drawdown(close[-min(n, self.dd_lookback):])
        ui = ulcer_index(close, self.dd_lookback)
        r.max_dd = float(mdd)
        r.ulcer = float(ui)
        r.expected_drawdown = self._score_expected_dd(mdd, ui)

        # ② Volatility
        vol_ann = volatility(close, self.vol_period)
        natr_val = float(natr(high, low, close, self.atr_period)[-1]) if n >= self.atr_period + 1 else 0.0
        if not np.isfinite(natr_val):
            natr_val = 0.0
        r.vol_annual = float(vol_ann)
        r.natr_val = natr_val
        r.volatility_risk = self._score_volatility(vol_ann, natr_val)

        # ③ Failure Probability（趋势破坏概率）
        r.failure_probability = self._score_failure_prob(close)

        # ④ Rotation Risk（轮动风险）
        r.rotation_risk = self._score_rotation_risk(close)

        # ⑤ Concentration Risk（集中度风险）- 用成交额集中度近似
        r.concentration_risk = self._score_concentration(df)

        # ⑥ Market Correlation
        beta_val = 1.0
        corr_val = 0.0
        if benchmark is not None and len(benchmark) > self.dd_lookback:
            beta_val = beta_coef(close, benchmark, self.dd_lookback)
            from etf_alpha_engine.indicators import rolling_corr
            corr_val = rolling_corr(close, benchmark, self.dd_lookback)
        r.beta = float(beta_val)
        r.market_correlation = self._score_market_corr(beta_val, corr_val)

        # 综合
        final = (
            r.expected_drawdown * self.w_dd +
            r.volatility_risk * self.w_vol +
            r.failure_probability * self.w_fail +
            r.rotation_risk * self.w_rot +
            r.concentration_risk * self.w_conc +
            r.market_correlation * self.w_corr
        )
        r.risk_score = float(np.clip(final, 0, 100))

        # 仓位建议
        r.suggested_position = self._suggest_position(r.risk_score)

        # 止损止盈
        atr_val = float(atr(high, low, close, self.atr_period)[-1]) if n >= self.atr_period + 1 else 0.0
        if not np.isfinite(atr_val) or atr_val <= 0:
            atr_val = float(np.std(np.diff(close))) if n > 2 else 1.0
        r.stop_loss = float(np.clip(atr_val * self.stop_atr_mult / max(close[-1], 1e-6), 0.03, 0.15))
        r.take_profit = float(np.clip(atr_val * self.tp_atr_mult / max(close[-1], 1e-6), 0.08, 0.40))

        r.reasons = self._build_reasons(r)
        return r

    # ------------------------------------------------------------------
    # 子维度1: Expected Drawdown
    # ------------------------------------------------------------------
    def _score_expected_dd(self, mdd, ui) -> float:
        s = 0.0
        # 最大回撤
        if mdd > 0.25:
            s += 35
        elif mdd > 0.15:
            s += 25
        elif mdd > 0.08:
            s += 15
        elif mdd > 0.03:
            s += 8
        # Ulcer
        if ui > 15:
            s += 30
        elif ui > 8:
            s += 20
        elif ui > 3:
            s += 10
        return float(np.clip(s, 0, 100))

    # ------------------------------------------------------------------
    # 子维度2: Volatility
    # ------------------------------------------------------------------
    def _score_volatility(self, vol_ann, natr_val) -> float:
        s = 0.0
        if vol_ann > 0.5:
            s += 50
        elif vol_ann > 0.35:
            s += 35
        elif vol_ann > 0.25:
            s += 20
        elif vol_ann > 0.15:
            s += 10
        # NATR
        if natr_val > 4:
            s += 50
        elif natr_val > 2.5:
            s += 35
        elif natr_val > 1.5:
            s += 20
        elif natr_val > 0.8:
            s += 10
        return float(np.clip(s, 0, 100))

    # ------------------------------------------------------------------
    # 子维度3: Failure Probability
    # ------------------------------------------------------------------
    def _score_failure_prob(self, close) -> float:
        n = len(close)
        if n < 30:
            return 50.0
        # 趋势破坏：价格跌破MA20且MA20下行
        ma20 = np.convolve(close, np.ones(20) / 20, mode="valid")
        if len(ma20) < 5:
            return 50.0
        below_ma = close[-1] < ma20[-1]
        ma20_down = ma20[-1] < ma20[-5]
        # 近期动量
        r10 = close[-1] / close[-11] - 1 if n > 10 else 0
        s = 30.0
        if below_ma:
            s += 25
        if ma20_down:
            s += 20
        if r10 < -0.05:
            s += 25
        elif r10 < -0.02:
            s += 10
        elif r10 > 0.03:
            s -= 10
        return float(np.clip(s, 0, 100))

    # ------------------------------------------------------------------
    # 子维度4: Rotation Risk
    # ------------------------------------------------------------------
    def _score_rotation_risk(self, close) -> float:
        n = len(close)
        if n < 30:
            return 50.0
        # 短期动量衰减 = 轮动风险
        r5 = close[-1] / close[-6] - 1 if n > 5 else 0
        r20 = close[-1] / close[-21] - 1 if n > 20 else 0
        # 短期强但中期弱 = 高轮动风险
        if r5 > 0.05 and r20 < 0:
            return 75.0
        if r5 < -0.03 and r20 > 0.05:
            return 65.0  # 短期回调但中期仍强，中等轮动风险
        if r5 > 0 and r20 > 0:
            return 30.0  # 趋势一致
        if r5 < 0 and r20 < 0:
            return 70.0  # 趋势破坏
        return 50.0

    # ------------------------------------------------------------------
    # 子维度5: Concentration Risk
    # ------------------------------------------------------------------
    def _score_concentration(self, df) -> float:
        # 用成交额集中度近似（如果有amount）
        if "amount" not in df.columns:
            return 40.0
        amt = df["amount"].values.astype(float)
        if len(amt) < 10:
            return 40.0
        # 近期成交额方差大 = 集中度高
        recent = amt[-10:]
        cv = float(np.std(recent) / max(np.mean(recent), 1e-6))
        # 变异系数高 = 流动性不稳定 = 集中度风险
        if cv > 1.0:
            return 70.0
        elif cv > 0.5:
            return 50.0
        return 30.0

    # ------------------------------------------------------------------
    # 子维度6: Market Correlation
    # ------------------------------------------------------------------
    def _score_market_corr(self, beta, corr) -> float:
        s = 30.0
        # Beta过高 = 系统性风险高
        if beta > 1.5:
            s += 40
        elif beta > 1.2:
            s += 25
        elif beta > 1.0:
            s += 15
        elif beta < 0.5:
            s += 5  # 低beta反而安全
        # 相关性
        if corr > 0.8:
            s += 30
        elif corr > 0.6:
            s += 20
        elif corr > 0.4:
            s += 10
        return float(np.clip(s, 0, 100))

    # ------------------------------------------------------------------
    # 仓位建议
    # ------------------------------------------------------------------
    def _suggest_position(self, risk_score: float) -> float:
        if risk_score < 30:
            return self.position_map.get("low", 1.0)
        if risk_score < 55:
            return self.position_map.get("medium", 0.6)
        if risk_score < 70:
            return self.position_map.get("high", 0.3)
        return self.position_map.get("extreme", 0.0)

    def _build_reasons(self, r: RiskResult) -> list:
        parts = []
        if r.expected_drawdown >= 60:
            parts.append(f"回撤风险高(MDD={r.max_dd*100:.1f}%)")
        if r.volatility_risk >= 60:
            parts.append(f"波动大(vol={r.vol_annual*100:.0f}%)")
        if r.failure_probability >= 60:
            parts.append("趋势破坏风险")
        if r.rotation_risk >= 60:
            parts.append("轮动风险")
        if r.concentration_risk >= 60:
            parts.append("流动性集中风险")
        if r.market_correlation >= 60:
            parts.append(f"市场相关高(beta={r.beta:.2f})")
        if r.risk_score < 30:
            parts.append("风险可控")
        parts.append(f"建议仓位{r.suggested_position*100:.0f}%")
        return parts
