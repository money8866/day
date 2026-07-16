#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Step 8: Risk Engine 风险引擎
================================
评估:
  - Rotation Risk (轮动风险)
  - Market Risk (市场风险)
  - Theme Failure Risk (主题失败风险)
  - Leader Failure Risk (龙头失败风险)
  - Liquidity Risk (流动性风险)
  - Drawdown Risk (回撤风险)

Output:
  - RiskScore (0-100, higher = riskier)
  - Suggested Position (建议仓位)
  - StopLoss (止损)
  - Reduce Position Trigger (减仓触发)

硬过滤器: RiskScore <= 40
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np
import pandas as pd

from etf_winner_prediction.indicators import (
    atr, natr, volatility, max_drawdown, ulcer_index,
    beta as beta_coef, rolling_corr,
)


@dataclass
class RiskResult:
    etf_code: str = ""
    risk_score: float = 50.0
    # 子维度
    rotation_risk: float = 0.0
    market_risk: float = 0.0
    theme_failure_risk: float = 0.0
    leader_failure_risk: float = 0.0
    liquidity_risk: float = 0.0
    drawdown_risk: float = 0.0
    # 原始指标
    natr_val: float = 0.0
    max_dd: float = 0.0
    ulcer: float = 0.0
    beta: float = 1.0
    vol_annual: float = 0.0
    # 输出
    suggested_position: float = 0.5
    stop_loss: float = 0.0
    reduce_position_trigger: float = 0.0
    reasons: list = field(default_factory=list)


class RiskEngine:
    """风险引擎 - Step 8"""

    def __init__(self, config: dict):
        self.cfg = config.get("risk_engine", {})
        self.w_rotation = self.cfg.get("rotation_risk_weight", 0.20)
        self.w_market = self.cfg.get("market_risk_weight", 0.20)
        self.w_theme = self.cfg.get("theme_failure_weight", 0.15)
        self.w_leader = self.cfg.get("leader_failure_weight", 0.15)
        self.w_liquidity = self.cfg.get("liquidity_risk_weight", 0.15)
        self.w_drawdown = self.cfg.get("drawdown_risk_weight", 0.15)
        self.atr_period = self.cfg.get("atr_period", 14)
        self.dd_lookback = self.cfg.get("drawdown_lookback", 60)
        self.vol_period = self.cfg.get("vol_period", 20)
        self.position_map = self.cfg.get("position_by_risk", {
            "low": 1.0, "medium": 0.6, "high": 0.3, "extreme": 0.0
        })
        self.stop_atr_mult = self.cfg.get("stop_loss_atr_multiple", 2.0)
        self.reduce_atr_mult = self.cfg.get("reduce_position_atr_multiple", 4.0)
        self.max_risk = self.cfg.get("max_risk_score", 40)

    def score(self, etf_data: Dict[str, pd.DataFrame],
              benchmark_close: Optional[np.ndarray] = None,
              theme_rotation_prob: Dict[str, float] = None,
              theme_remaining_days: Dict[str, int] = None,
              leader_failure_risk: Dict[str, float] = None,
              etf_theme_map: Dict[str, str] = None) -> Dict[str, RiskResult]:
        """对所有ETF计算风险"""
        results = {}
        for code, df in etf_data.items():
            if df is None or df.empty or len(df) < 30:
                continue
            theme_name = etf_theme_map.get(code, "") if etf_theme_map else ""
            rot_prob = theme_rotation_prob.get(theme_name, 0.3) if theme_rotation_prob else 0.3
            rem_days = theme_remaining_days.get(theme_name, 20) if theme_remaining_days else 20
            lf_risk = leader_failure_risk.get(code, 50.0) if leader_failure_risk else 50.0
            r = self._score_one(code, df, benchmark_close, rot_prob, rem_days, lf_risk)
            results[code] = r
        return results

    def _score_one(self, code, df, benchmark, rot_prob, rem_days, lf_risk) -> RiskResult:
        r = RiskResult(etf_code=code)
        close = np.asarray(df["close"].values, dtype=np.float64)
        high = np.asarray(df["high"].values, dtype=np.float64) if "high" in df.columns else close
        low = np.asarray(df["low"].values, dtype=np.float64) if "low" in df.columns else close
        n = len(close)

        # ① Rotation Risk
        r.rotation_risk = self._score_rotation_risk(close, rot_prob)

        # ② Market Risk
        beta_val = 1.0
        if benchmark is not None and len(benchmark) > self.dd_lookback:
            beta_val = beta_coef(close, benchmark, self.dd_lookback)
        r.beta = float(beta_val)
        r.market_risk = self._score_market_risk(beta_val, close)

        # ③ Theme Failure Risk
        r.theme_failure_risk = self._score_theme_failure(close, rem_days)

        # ④ Leader Failure Risk
        r.leader_failure_risk = float(np.clip(lf_risk, 0, 100))

        # ⑤ Liquidity Risk
        r.liquidity_risk = self._score_liquidity_risk(df)

        # ⑥ Drawdown Risk
        mdd = max_drawdown(close[-min(n, self.dd_lookback):])
        ui = ulcer_index(close, self.dd_lookback)
        r.max_dd = float(mdd)
        r.ulcer = float(ui)
        r.drawdown_risk = self._score_drawdown_risk(mdd, ui)

        # 波动率
        vol_ann = volatility(close, self.vol_period)
        natr_val = float(natr(high, low, close, self.atr_period)[-1]) if n >= self.atr_period + 1 else 0.0
        if not np.isfinite(natr_val):
            natr_val = 0.0
        r.vol_annual = float(vol_ann)
        r.natr_val = natr_val

        # 综合
        final = (
            r.rotation_risk * self.w_rotation +
            r.market_risk * self.w_market +
            r.theme_failure_risk * self.w_theme +
            r.leader_failure_risk * self.w_leader +
            r.liquidity_risk * self.w_liquidity +
            r.drawdown_risk * self.w_drawdown
        )
        r.risk_score = float(np.clip(final, 0, 100))

        # 仓位建议
        r.suggested_position = self._suggest_position(r.risk_score)

        # 止损
        atr_val = float(atr(high, low, close, self.atr_period)[-1]) if n >= self.atr_period + 1 else 0.0
        if not np.isfinite(atr_val) or atr_val <= 0:
            atr_val = float(np.std(np.diff(close))) if n > 2 else 1.0
        r.stop_loss = float(np.clip(atr_val * self.stop_atr_mult / max(close[-1], 1e-6), 0.03, 0.15))
        r.reduce_position_trigger = float(np.clip(atr_val * self.reduce_atr_mult / max(close[-1], 1e-6), 0.06, 0.25))

        r.reasons = self._build_reasons(r)
        return r

    def _score_rotation_risk(self, close, rot_prob) -> float:
        n = len(close)
        if n < 30:
            return 50.0
        r5 = close[-1] / close[-6] - 1 if n > 5 else 0
        r20 = close[-1] / close[-21] - 1 if n > 20 else 0
        s = 30.0
        if r5 > 0.05 and r20 < 0:
            s = 75.0
        elif r5 < -0.03 and r20 > 0.05:
            s = 65.0
        elif r5 > 0 and r20 > 0:
            s = 30.0
        elif r5 < 0 and r20 < 0:
            s = 70.0
        # 轮动概率加成
        s += rot_prob * 20
        return float(np.clip(s, 0, 100))

    def _score_market_risk(self, beta, close) -> float:
        s = 30.0
        if beta > 1.5:
            s += 40
        elif beta > 1.2:
            s += 25
        elif beta > 1.0:
            s += 15
        elif beta < 0.5:
            s -= 10
        return float(np.clip(s, 0, 100))

    def _score_theme_failure(self, close, remaining_days) -> float:
        n = len(close)
        if n < 30:
            return 50.0
        ma20 = np.convolve(close, np.ones(20) / 20, mode="valid")
        if len(ma20) < 5:
            return 50.0
        below_ma = close[-1] < ma20[-1]
        ma20_down = ma20[-1] < ma20[-5]
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
        if remaining_days < 20:
            s += 15
        return float(np.clip(s, 0, 100))

    def _score_liquidity_risk(self, df) -> float:
        if "amount" not in df.columns:
            return 40.0
        amt = df["amount"].values.astype(float)
        if len(amt) < 10:
            return 40.0
        recent = amt[-10:]
        cv = float(np.std(recent) / max(np.mean(recent), 1e-6))
        if cv > 1.0:
            return 70.0
        elif cv > 0.5:
            return 50.0
        return 30.0

    def _score_drawdown_risk(self, mdd, ui) -> float:
        s = 0.0
        if mdd > 0.25:
            s += 35
        elif mdd > 0.15:
            s += 25
        elif mdd > 0.08:
            s += 15
        elif mdd > 0.03:
            s += 8
        if ui > 15:
            s += 30
        elif ui > 8:
            s += 20
        elif ui > 3:
            s += 10
        return float(np.clip(s, 0, 100))

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
        if r.rotation_risk >= 60:
            parts.append("轮动风险")
        if r.market_risk >= 60:
            parts.append(f"市场风险(beta={r.beta:.2f})")
        if r.theme_failure_risk >= 60:
            parts.append("主题失败风险")
        if r.leader_failure_risk >= 60:
            parts.append("龙头失败风险")
        if r.liquidity_risk >= 60:
            parts.append("流动性风险")
        if r.drawdown_risk >= 60:
            parts.append(f"回撤风险(MDD={r.max_dd*100:.1f}%)")
        if r.risk_score < 30:
            parts.append("风险可控")
        parts.append(f"建议仓位{r.suggested_position*100:.0f}%")
        parts.append(f"止损-{r.stop_loss*100:.1f}%")
        return parts