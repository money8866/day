#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Step 3: Lifecycle Prediction 生命周期预测
===========================================
动态预测主题生命周期，不用固定阈值。

8个阶段:
  Seed -> Birth -> Acceleration -> Expansion -> Peak -> Distribution -> Decline -> Dead

对每个阶段预测:
  - 进入下一阶段概率
  - 剩余趋势天数
  - 置信度

拒绝: Peak / Distribution / Decline / Dead
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from etf_winner_prediction.indicators import ema, slope, new_high_count


@dataclass
class LifecycleResult:
    stage: str = "Neutral"
    stage_signal: float = 0.0          # stronger = more bullish stage
    remaining_trend_days: int = 0
    next_stage_probability: float = 0.0
    next_stage: str = ""
    confidence: float = 0.0
    # 子指标
    ema_slope: float = 0.0
    momentum: float = 0.0
    relative_strength: float = 0.0
    leader_breadth: float = 0.0
    capital_flow: float = 0.0
    rolling_new_highs: int = 0
    trend_elapsed_days: int = 0
    # 决策
    is_buy_stage: bool = False
    is_reject_stage: bool = False
    reasons: list = field(default_factory=list)


STAGES = ["Seed", "Birth", "Acceleration", "Expansion",
          "Peak", "Distribution", "Decline", "Dead"]
STAGE_IDX = {s: i for i, s in enumerate(STAGES)}

# 每个阶段进入下一阶段的概率
NEXT_STAGE_BASE = {
    "Seed": 0.40, "Birth": 0.35, "Acceleration": 0.25,
    "Expansion": 0.20, "Peak": 0.50, "Distribution": 0.60,
    "Decline": 0.45, "Dead": 0.10,
}
NEXT_STAGE_MAP = {
    "Seed": "Birth", "Birth": "Acceleration", "Acceleration": "Expansion",
    "Expansion": "Peak", "Peak": "Distribution", "Distribution": "Decline",
    "Decline": "Dead", "Dead": "Dead",
}


class LifecyclePredictor:
    """生命周期预测器 - Step 3"""

    def __init__(self, config: dict):
        self.cfg = config.get("lifecycle", {})
        self.ema_fast = self.cfg.get("ema_fast", 5)
        self.ema_mid = self.cfg.get("ema_mid", 20)
        self.ema_slow = self.cfg.get("ema_slow", 60)
        self.stage_duration = self.cfg.get("stage_duration", {})
        self.stage_prob = self.cfg.get("stage_probability", {})
        self.buy_stages = set(self.cfg.get("buy_stages", ["Birth", "Acceleration", "Expansion"]))
        self.reject_stages = set(self.cfg.get("reject_stages",
                                               ["Peak", "Distribution", "Decline", "Dead"]))

    def score(self, daily: pd.DataFrame,
              universe: Dict[str, List[str]],
              moneyflow: Optional[pd.DataFrame] = None) -> Dict[str, LifecycleResult]:
        """对所有主题预测生命周期"""
        results = {}
        for tname, codes in universe.items():
            if len(codes) < 3:
                continue
            r = self._score_one(daily, codes, moneyflow)
            r.reasons = self._build_reasons(r)
            results[tname] = r
        return results

    def score_etf(self, etf_df: pd.DataFrame) -> LifecycleResult:
        """对单只ETF预测生命周期"""
        if etf_df is None or etf_df.empty:
            return LifecycleResult()
        daily = etf_df.copy()
        daily["ts_code"] = "_ETF_"
        return self._score_one(daily, ["_ETF_"], None)

    def _score_one(self, daily, codes, moneyflow) -> LifecycleResult:
        r = LifecycleResult()
        sub = daily[daily["ts_code"].isin(codes)]
        if sub.empty:
            return r

        price = sub.groupby("trade_date")["close"].mean().sort_index()
        n = len(price)
        if n < self.ema_slow + 5:
            r.stage = "Seed"
            return r

        prices = price.values.astype(float)

        # ① EMA斜率
        ema_mid_vals = ema(prices, self.ema_mid)
        r.ema_slope = float((ema_mid_vals[-1] - ema_mid_vals[-6]) / max(ema_mid_vals[-6], 1e-6))

        # ② 动量
        r5 = float(prices[-1] / prices[-6] - 1) if n > 5 else 0
        r10 = float(prices[-1] / prices[-11] - 1) if n > 10 else 0
        r20 = float(prices[-1] / prices[-21] - 1) if n > 20 else 0
        r.momentum = r5 * 0.4 + r10 * 0.35 + r20 * 0.25

        # ③ 相对强度
        ema_fast_vals = ema(prices, self.ema_fast)
        ema_slow_vals = ema(prices, self.ema_slow)
        if prices[-1] > ema_fast_vals[-1] > ema_mid_vals[-1] > ema_slow_vals[-1]:
            r.relative_strength = 80.0
        elif prices[-1] > ema_mid_vals[-1] > ema_slow_vals[-1]:
            r.relative_strength = 60.0
        elif prices[-1] > ema_slow_vals[-1]:
            r.relative_strength = 40.0
        else:
            r.relative_strength = 20.0

        # ④ 龙头宽度
        latest_day = sub["trade_date"].max()
        above_count = 0
        total_count = 0
        for code, sd in sub.groupby("ts_code"):
            sd = sd.sort_values("trade_date")
            if len(sd) < self.ema_mid:
                continue
            total_count += 1
            ema20 = ema(sd["close"].values.astype(float), self.ema_mid)
            if sd["close"].iloc[-1] > ema20[-1]:
                above_count += 1
        r.leader_breadth = float(above_count / total_count * 100) if total_count > 0 else 50.0

        # ⑤ 资金流
        amt = sub.groupby("trade_date")["amount"].sum().sort_index()
        if len(amt) >= 20:
            amt_5 = amt.iloc[-5:].mean()
            amt_20 = amt.iloc[-20:].mean()
            r.capital_flow = float((amt_5 / max(amt_20, 1) - 1.0)) if amt_20 > 0 else 0.0
        else:
            r.capital_flow = 0.0

        # ⑥ 滚动新高
        r.rolling_new_highs = new_high_count(prices, 20)

        # ===== 阶段判断 =====
        stage = self._classify_stage(r.ema_slope, r.momentum, r.relative_strength,
                                      r.leader_breadth, r.capital_flow, r.rolling_new_highs)
        r.stage = stage
        r.is_buy_stage = stage in self.buy_stages
        r.is_reject_stage = stage in self.reject_stages

        # 趋势已持续天数
        r.trend_elapsed_days = self._calc_elapsed(prices, n)

        # 剩余趋势天数
        r.remaining_trend_days = self._estimate_remaining(stage, r.momentum, r.relative_strength, r.capital_flow)

        # 进入下一阶段概率
        r.next_stage = NEXT_STAGE_MAP.get(stage, "Dead")
        r.next_stage_probability = self._estimate_next_prob(stage, r.momentum, r.ema_slope, r.capital_flow)

        # 置信度
        r.confidence = self._estimate_confidence(stage, r)

        # 阶段信号（0-100，越高越多头）
        r.stage_signal = self._stage_to_signal(stage, r)

        return r

    def _classify_stage(self, ema_slope, momentum, rs, breadth, capital, new_highs) -> str:
        up_trend = ema_slope > 0.005 and momentum > 0.01
        strong_up = ema_slope > 0.015 and momentum > 0.03
        down_trend = ema_slope < -0.005 and momentum < -0.01
        strong_down = ema_slope < -0.015 and momentum < -0.03

        if strong_down and new_highs == 0 and capital < -0.2 and rs <= 20:
            return "Dead"
        if down_trend and rs < 40:
            return "Decline"
        if (rs >= 60 and -0.003 < ema_slope < 0.005
            and -0.01 < momentum < 0.01 and capital < 0):
            return "Distribution"
        if (rs >= 70 and momentum < 0.005 and ema_slope < 0.01
            and new_highs < 3):
            return "Peak"
        if strong_up and breadth >= 60 and capital > 0.1 and new_highs >= 5:
            return "Acceleration"
        if up_trend and breadth >= 50 and rs >= 60:
            return "Expansion"
        if ema_slope > 0 and momentum > 0 and 35 <= rs < 60:
            return "Birth"
        if (-0.005 < ema_slope < 0.005 and -0.01 < momentum < 0.02
            and 30 <= rs <= 50):
            return "Seed"
        if up_trend:
            return "Expansion"
        return "Decline"

    def _calc_elapsed(self, prices, n) -> int:
        if n < 5:
            return 0
        lookback = min(n, 120)
        recent = prices[-lookback:]
        low_idx = int(np.argmin(recent))
        if low_idx == lookback - 1:
            return 0
        return lookback - low_idx - 1

    def _estimate_remaining(self, stage, momentum, rs, capital) -> int:
        base = self.stage_duration.get(stage, 15)
        if momentum > 0.03:
            base += 10
        if rs >= 70:
            base += 5
        if capital > 0.1:
            base += 5
        elif capital < -0.1:
            base -= 5
        return int(np.clip(base, 0, 60))

    def _estimate_next_prob(self, stage, momentum, ema_slope, capital) -> float:
        base = NEXT_STAGE_BASE.get(stage, 0.3)
        if momentum > 0.02:
            base += 0.10
        if ema_slope > 0.01:
            base += 0.05
        if capital < -0.1:
            base += 0.10  # 资金流出加速进入下一阶段
        return float(np.clip(base, 0.05, 0.85))

    def _estimate_confidence(self, stage, r: LifecycleResult) -> float:
        base = self.stage_prob.get(stage, 0.4)
        if abs(r.ema_slope) > 0.01:
            base += 0.05
        if r.leader_breadth > 60:
            base += 0.05
        if abs(r.capital_flow) > 0.1:
            base += 0.05
        return float(np.clip(base, 0.1, 0.95))

    def _stage_to_signal(self, stage, r: LifecycleResult) -> float:
        """阶段转信号分数（0-100）"""
        base = {"Seed": 30, "Birth": 65, "Acceleration": 85, "Expansion": 75,
                "Peak": 35, "Distribution": 20, "Decline": 10, "Dead": 5}.get(stage, 40)
        if r.momentum > 0.02:
            base += 5
        if r.ema_slope > 0.01:
            base += 5
        if r.leader_breadth >= 60:
            base += 5
        return float(np.clip(base, 0, 100))

    def _build_reasons(self, r: LifecycleResult) -> list:
        parts = [f"阶段={r.stage}"]
        if r.ema_slope > 0.01:
            parts.append("EMA上行")
        elif r.ema_slope < -0.01:
            parts.append("EMA下行")
        if r.momentum > 0.03:
            parts.append("动量强")
        if r.leader_breadth >= 60:
            parts.append(f"宽度扩散({r.leader_breadth:.0f}%)")
        if r.capital_flow > 0.1:
            parts.append("资金流入")
        elif r.capital_flow < -0.1:
            parts.append("资金流出")
        parts.append(f"已持续{r.trend_elapsed_days}天")
        parts.append(f"预计剩余{r.remaining_trend_days}天")
        parts.append(f"下一阶段概率{r.next_stage_probability:.0%}")
        return parts