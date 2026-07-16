#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Buy/Sell Rules Engine 买卖规则引擎
================================================
BUY RULES - Buy only when ALL:
  - Market Score > 70
  - Theme Rank <= 3
  - Lifecycle in (Birth, Acceleration, Expansion)
  - ETF Alpha > 85
  - Leader Score > 80
  - Expected Return > 10%
  - Trend Duration > 20 days

SELL RULES - Sell immediately when any TWO occur:
  - Lifecycle enters Distribution
  - Leader breaks MA20
  - ETF closes below MA20 for 3 days
  - Theme Rank falls below 5
  - Expected Return < 5%
  - Risk Score > 70
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd

from etf_alpha_engine.composite import FinalETFResult
from etf_alpha_engine.theme_lifecycle import LifecycleResult
from etf_alpha_engine.leader_confirm import LeaderConfirmResult


@dataclass
class SignalResult:
    """买卖信号结果"""
    buy: bool = False
    hold: bool = False
    sell: bool = False
    sell_triggers: list = field(default_factory=list)
    buy_reasons: list = field(default_factory=list)
    sell_reasons: list = field(default_factory=list)


class RulesEngine:
    """买卖规则引擎

    独立可运行，输入各模块分数，输出买卖信号。
    所有阈值可参数优化。
    """

    def __init__(self, config: dict):
        self.cfg = config.get("rules", {})
        self.buy_cfg = self.cfg.get("buy", {})
        self.sell_cfg = self.cfg.get("sell", {})
        # Buy thresholds
        self.buy_market_min = self.buy_cfg.get("market_score_min", 70)
        self.buy_theme_rank_max = self.buy_cfg.get("theme_rank_max", 3)
        self.buy_etf_alpha_min = self.buy_cfg.get("etf_alpha_min", 85)
        self.buy_leader_min = self.buy_cfg.get("leader_score_min", 80)
        self.buy_exp_return_min = self.buy_cfg.get("expected_return_min", 0.10)
        self.buy_trend_dur_min = self.buy_cfg.get("trend_duration_min", 20)
        self.buy_lifecycle = set(self.buy_cfg.get("allowed_lifecycle",
                                                    ["Birth", "Acceleration", "Expansion"]))
        # Sell config
        self.sell_triggers_cfg = self.sell_cfg.get("triggers", {})
        self.sell_min_triggers = self.sell_cfg.get("min_triggers_to_sell", 2)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def evaluate(self,
                 r: FinalETFResult,
                 etf_df: Optional[pd.DataFrame] = None,
                 leader_df: Optional[pd.DataFrame] = None,
                 ) -> SignalResult:
        """评估买卖信号"""
        sig = SignalResult()

        # ===== Buy Rules =====
        buy_reasons = []
        buy_pass = True

        if r.market_score <= self.buy_market_min:
            buy_pass = False
            buy_reasons.append(f"市场分{r.market_score}<={self.buy_market_min}")
        else:
            buy_reasons.append(f"市场强({r.market_score})")

        if r.theme_rank > self.buy_theme_rank_max:
            buy_pass = False
            buy_reasons.append(f"主题排名{r.theme_rank}>{self.buy_theme_rank_max}")
        else:
            buy_reasons.append(f"主题排名{r.theme_rank}")

        if r.lifecycle not in self.buy_lifecycle:
            buy_pass = False
            buy_reasons.append(f"阶段{r.lifecycle}不可买")
        else:
            buy_reasons.append(f"阶段可买({r.lifecycle})")

        if r.etf_alpha < self.buy_etf_alpha_min:
            buy_pass = False
            buy_reasons.append(f"Alpha{r.etf_alpha}<{self.buy_etf_alpha_min}")
        else:
            buy_reasons.append(f"Alpha强({r.etf_alpha})")

        if r.leader_score < self.buy_leader_min:
            buy_pass = False
            buy_reasons.append(f"龙头分{r.leader_score}<{self.buy_leader_min}")
        else:
            buy_reasons.append(f"龙头强({r.leader_score})")

        if r.expected_return < self.buy_exp_return_min:
            buy_pass = False
            buy_reasons.append(f"预期收益{r.expected_return*100:.1f}%<{self.buy_exp_return_min*100:.0f}%")
        else:
            buy_reasons.append(f"预期收益{r.expected_return*100:.1f}%")

        if r.trend_duration < self.buy_trend_dur_min:
            buy_pass = False
            buy_reasons.append(f"趋势时长{r.trend_duration}<{self.buy_trend_dur_min}")
        else:
            buy_reasons.append(f"趋势时长{r.trend_duration}天")

        sig.buy = buy_pass
        sig.buy_reasons = buy_reasons

        # ===== Sell Rules =====
        sell_triggers = []

        # 1. Lifecycle enters Distribution
        if self.sell_triggers_cfg.get("lifecycle_distribution", True):
            if r.lifecycle in ("Distribution", "Peak", "Decline", "Dead"):
                sell_triggers.append(f"生命周期恶化({r.lifecycle})")

        # 2. Leader breaks MA20
        if self.sell_triggers_cfg.get("leader_break_ma20", True) and leader_df is not None:
            if self._leader_breaks_ma20(leader_df):
                sell_triggers.append("龙头跌破MA20")

        # 3. ETF closes below MA20 for 3 days
        if self.sell_triggers_cfg.get("etf_below_ma20_3d", True) and etf_df is not None:
            if self._etf_below_ma20_3d(etf_df):
                sell_triggers.append("ETF连续3日跌破MA20")

        # 4. Theme Rank falls below 5
        if self.sell_triggers_cfg.get("theme_rank_below", 5):
            threshold = self.sell_triggers_cfg.get("theme_rank_below", 5)
            if r.theme_rank > threshold:
                sell_triggers.append(f"主题排名{r.theme_rank}>{threshold}")

        # 5. Expected Return < 5%
        if self.sell_triggers_cfg.get("expected_return_below", 0.05):
            threshold = self.sell_triggers_cfg.get("expected_return_below", 0.05)
            if r.expected_return < threshold:
                sell_triggers.append(f"预期收益{r.expected_return*100:.1f}%<{threshold*100:.0f}%")

        # 6. Risk Score > 70
        if self.sell_triggers_cfg.get("risk_score_above", 70):
            threshold = self.sell_triggers_cfg.get("risk_score_above", 70)
            if r.risk_score > threshold:
                sell_triggers.append(f"风险分{r.risk_score}>{threshold}")

        sig.sell_triggers = sell_triggers
        sig.sell = len(sell_triggers) >= self.sell_min_triggers
        sig.sell_reasons = sell_triggers

        # Hold = 既不买也不卖，但分数高
        if not sig.buy and not sig.sell:
            if r.etf_alpha >= 70 and r.lifecycle in self.buy_lifecycle:
                sig.hold = True

        return sig

    # ------------------------------------------------------------------
    # 龙头是否跌破MA20
    # ------------------------------------------------------------------
    def _leader_breaks_ma20(self, leader_df: pd.DataFrame) -> bool:
        if leader_df is None or leader_df.empty:
            return False
        df = leader_df.sort_values("trade_date")
        if len(df) < 25:
            return False
        close = df["close"].values.astype(float)
        ma20 = np.mean(close[-20:])
        return close[-1] < ma20

    # ------------------------------------------------------------------
    # ETF是否连续3日跌破MA20
    # ------------------------------------------------------------------
    def _etf_below_ma20_3d(self, etf_df: pd.DataFrame) -> bool:
        if etf_df is None or etf_df.empty:
            return False
        df = etf_df.sort_values("trade_date")
        if len(df) < 23:
            return False
        close = df["close"].values.astype(float)
        # 计算MA20序列
        ma20 = np.convolve(close, np.ones(20) / 20, mode="valid")
        if len(ma20) < 3:
            return False
        # 最近3日是否都低于MA20
        return np.all(close[-3:] < ma20[-3:])
