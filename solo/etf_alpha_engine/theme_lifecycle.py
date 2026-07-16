#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Module 3: Theme Lifecycle Engine 主题生命周期引擎
================================================
Each theme belongs to one stage:
  Seed -> Birth -> Acceleration -> Expansion -> Peak -> Distribution -> Decline -> Dead

Multiple indicators used:
  - EMA slope
  - Momentum
  - RS (relative strength)
  - Leader breadth
  - ETF momentum
  - Capital flow
  - Rolling new highs

Estimates:
  - Remaining Trend Duration (days)
  - Trend Probability (%)
  - Rotation Probability (%)

Buy only: Birth / Acceleration / Early Expansion
Avoid: Peak / Distribution / Decline
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from etf_alpha_engine.indicators import ema, slope, new_high_count, consecutive_up_days


@dataclass
class LifecycleResult:
    """生命周期结果"""
    stage: str = "Neutral"
    stage_bonus: float = 0.0
    trend_elapsed_days: int = 0           # 实际已持续天数（从最低点算起）
    remaining_trend_duration: int = 0     # 剩余趋势天数（估计）
    trend_probability: float = 0.0        # 趋势概率%
    rotation_probability: float = 0.0     # 轮动概率%
    # 子指标
    ema_slope: float = 0.0
    momentum: float = 0.0
    relative_strength: float = 0.0
    leader_breadth: float = 0.0
    capital_flow: float = 0.0
    rolling_new_highs: int = 0
    is_buy_stage: bool = False
    is_avoid_stage: bool = False
    reasons: list = field(default_factory=list)


# 8阶段定义
STAGES = ["Seed", "Birth", "Acceleration", "Expansion",
          "Peak", "Distribution", "Decline", "Dead"]


class ThemeLifecycleEngine:
    """主题生命周期引擎

    独立可运行，输出每个主题的生命周期阶段和趋势估计。
    所有指标独立计算、可复用、可参数优化。
    """

    def __init__(self, config: dict):
        self.cfg = config.get("theme_lifecycle", {})
        self.ema_fast = self.cfg.get("ema_fast", 5)
        self.ema_mid = self.cfg.get("ema_mid", 20)
        self.ema_slow = self.cfg.get("ema_slow", 60)
        self.stage_bonus_map = self.cfg.get("stage_bonus", {})
        self.buy_stages = set(self.cfg.get("buy_stages", ["Birth", "Acceleration", "Expansion"]))
        self.avoid_stages = set(self.cfg.get("avoid_stages",
                                              ["Peak", "Distribution", "Decline"]))
        self.min_trend = self.cfg.get("min_trend_duration", 20)
        self.max_trend = self.cfg.get("max_trend_duration", 60)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def score(self,
              daily: pd.DataFrame,
              universe: Dict[str, List[str]],
              moneyflow: Optional[pd.DataFrame] = None,
              ) -> Dict[str, LifecycleResult]:
        """计算所有主题的生命周期"""
        results = {}
        for tname, codes in universe.items():
            if len(codes) < 3:
                continue
            r = self._score_one(daily, codes, moneyflow)
            r.reasons = self._build_reasons(r)
            results[tname] = r
        return results

    def score_etf(self, etf_df: pd.DataFrame) -> LifecycleResult:
        """对单只ETF计算生命周期（用ETF自身价格序列）"""
        if etf_df is None or etf_df.empty:
            return LifecycleResult()
        codes = ["_ETF_"]
        daily = etf_df.copy()
        daily["ts_code"] = "_ETF_"
        return self._score_one(daily, codes, None)

    # ------------------------------------------------------------------
    # 单主题生命周期
    # ------------------------------------------------------------------
    def _score_one(self, daily, codes, moneyflow) -> LifecycleResult:
        r = LifecycleResult()
        sub = daily[daily["ts_code"].isin(codes)]
        if sub.empty:
            return r

        price = sub.groupby("trade_date")["close"].mean().sort_index()
        n = len(price)
        if n < self.ema_slow + 5:
            r.stage = "Seed"
            r.stage_bonus = self.stage_bonus_map.get("Seed", 0)
            return r

        prices = price.values.astype(float)

        # ① EMA斜率
        ema_mid_vals = ema(prices, self.ema_mid)
        ema_slope = float((ema_mid_vals[-1] - ema_mid_vals[-6]) / max(ema_mid_vals[-6], 1e-6))
        r.ema_slope = ema_slope

        # ② 动量
        r5 = float(prices[-1] / prices[-6] - 1) if n > 5 else 0
        r10 = float(prices[-1] / prices[-11] - 1) if n > 10 else 0
        r20 = float(prices[-1] / prices[-21] - 1) if n > 20 else 0
        r.momentum = r5 * 0.4 + r10 * 0.35 + r20 * 0.25

        # ③ 相对强度（主题vs自身均线）
        ema_fast_vals = ema(prices, self.ema_fast)
        ema_slow_vals = ema(prices, self.ema_slow)
        rs = 0.0
        if prices[-1] > ema_fast_vals[-1] > ema_mid_vals[-1] > ema_slow_vals[-1]:
            rs = 80.0
        elif prices[-1] > ema_mid_vals[-1] > ema_slow_vals[-1]:
            rs = 60.0
        elif prices[-1] > ema_slow_vals[-1]:
            rs = 40.0
        else:
            rs = 20.0
        r.relative_strength = rs

        # ④ 龙头宽度（站上EMA20的股票比例）
        latest_day = sub["trade_date"].max()
        latest = sub[sub["trade_date"] == latest_day]
        above_count = 0
        total_count = 0
        for code, sd in sub.groupby("ts_code"):
            sd = sd.sort_values("trade_date")
            if len(sd) < self.ema_mid:
                continue
            total_count += 1
            ema20 = ema(sd["close"].values, self.ema_mid)
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
        stage = self._classify_stage(ema_slope, r.momentum, rs, r.leader_breadth,
                                       r.capital_flow, r.rolling_new_highs, n)
        r.stage = stage
        r.stage_bonus = self.stage_bonus_map.get(stage, 0.0)
        r.is_buy_stage = stage in self.buy_stages
        r.is_avoid_stage = stage in self.avoid_stages

        # ===== 实际已持续天数（从最低点算起） =====
        r.trend_elapsed_days = self._calc_trend_elapsed_days(prices, n)

        # ===== 估计剩余趋势时长 =====
        r.remaining_trend_duration = self._estimate_duration(stage, r.momentum, rs, n)

        # ===== 趋势概率 =====
        r.trend_probability = self._estimate_trend_prob(stage, r.momentum, ema_slope, rs)

        # ===== 轮动概率 =====
        r.rotation_probability = self._estimate_rotation_prob(stage, r.momentum, r.capital_flow)

        return r

    # ------------------------------------------------------------------
    # 阶段分类
    # ------------------------------------------------------------------
    def _classify_stage(self, ema_slope, momentum, rs, breadth, capital, new_highs, n) -> str:
        # 8阶段判断逻辑
        up_trend = ema_slope > 0.005 and momentum > 0.01
        strong_up = ema_slope > 0.015 and momentum > 0.03
        weak_up = 0 < ema_slope < 0.005 or -0.01 < momentum < 0.01
        down_trend = ema_slope < -0.005 and momentum < -0.01
        strong_down = ema_slope < -0.015 and momentum < -0.03

        # Dead: 严重下跌 + 无新高 + 资金流出
        if strong_down and new_highs == 0 and capital < -0.2 and rs <= 20:
            return "Dead"

        # Decline: 下跌趋势
        if down_trend and rs < 40:
            return "Decline"

        # Distribution: 高位滞涨/分歧（曾上涨现在EMA走平+宽度分化）
        if (rs >= 60 and -0.003 < ema_slope < 0.005
            and -0.01 < momentum < 0.01 and capital < 0):
            return "Distribution"

        # Peak: 顶部（高位+动量衰减+宽度仍高但新高减少）
        if (rs >= 70 and momentum < 0.005 and ema_slope < 0.01
            and new_highs < 3):
            return "Peak"

        # Acceleration: 加速上涨（强动量+宽度扩散+资金涌入）
        if strong_up and breadth >= 60 and capital > 0.1 and new_highs >= 5:
            return "Acceleration"

        # Expansion: 扩张（趋势确立+宽度增加）
        if up_trend and breadth >= 50 and rs >= 60:
            return "Expansion"

        # Birth: 启动（EMA刚转向上+动量刚转正）
        if ema_slope > 0 and momentum > 0 and 35 <= rs < 60:
            return "Birth"

        # Seed: 萌芽（底部企稳）
        if (-0.005 < ema_slope < 0.005 and -0.01 < momentum < 0.02
            and 30 <= rs <= 50):
            return "Seed"

        # 默认
        if up_trend:
            return "Expansion"
        if weak_up:
            return "Seed"
        return "Decline"

    # ------------------------------------------------------------------
    # 计算实际已持续天数（从近期最低点算起）
    # ------------------------------------------------------------------
    def _calc_trend_elapsed_days(self, prices, n) -> int:
        """从近期最低点至今的实际交易日数"""
        if n < 5:
            return 0
        lookback = min(n, 120)
        recent = prices[-lookback:]
        low_idx = int(np.argmin(recent))
        # 如果最低点就是今天，趋势未开始
        if low_idx == lookback - 1:
            return 0
        return lookback - low_idx - 1

    # ------------------------------------------------------------------
    # 估计剩余趋势时长
    # ------------------------------------------------------------------
    def _estimate_duration(self, stage, momentum, rs, n) -> int:
        base = {
            "Seed": 5, "Birth": 35, "Acceleration": 45, "Expansion": 30,
            "Peak": 10, "Distribution": 5, "Decline": 0, "Dead": 0,
        }.get(stage, 15)
        # 动量强 + RS高 -> 延长
        if momentum > 0.03:
            base += 10
        if rs >= 70:
            base += 5
        return int(np.clip(base, 0, self.max_trend))

    # ------------------------------------------------------------------
    # 趋势概率
    # ------------------------------------------------------------------
    def _estimate_trend_prob(self, stage, momentum, ema_slope, rs) -> float:
        base = {
            "Seed": 30, "Birth": 70, "Acceleration": 85, "Expansion": 75,
            "Peak": 35, "Distribution": 20, "Decline": 10, "Dead": 5,
        }.get(stage, 40)
        # 动量/斜率调整
        if momentum > 0.02:
            base += 5
        if ema_slope > 0.01:
            base += 5
        if rs >= 70:
            base += 5
        return float(np.clip(base, 0, 95))

    # ------------------------------------------------------------------
    # 轮动概率
    # ------------------------------------------------------------------
    def _estimate_rotation_prob(self, stage, momentum, capital) -> float:
        # 轮动概率：资金开始流出 + 动量减弱 = 高轮动概率
        base = {
            "Seed": 30, "Birth": 15, "Acceleration": 20, "Expansion": 35,
            "Peak": 70, "Distribution": 85, "Decline": 60, "Dead": 40,
        }.get(stage, 40)
        if capital < -0.1:
            base += 10
        if momentum < 0:
            base += 5
        return float(np.clip(base, 0, 95))

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
        parts.append(f"趋势概率{r.trend_probability:.0f}%")
        parts.append(f"已持续{r.trend_elapsed_days}天")
        parts.append(f"预估剩余{r.remaining_trend_duration}天")
        return parts
