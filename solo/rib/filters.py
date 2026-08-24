# -*- coding: utf-8 -*-
"""
过滤模块

  - MarketFilter: 市场环境过滤
  - ThemeFilter: 主题/行业增强
  - RiskRewardEngine: 盈亏比计算
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import RIB_CONFIG


# ═══════════════════════════════════════════════════════
# 市场环境过滤
# ═══════════════════════════════════════════════════════

@dataclass
class MarketSnapshot:
    """市场快照。"""
    index_price: float = 0.0
    index_change_pct: float = 0.0
    index_ma20_slope: float = 0.0
    index_ma60_slope: float = 0.0
    advance_decline_ratio: float = 0.0
    limit_up_count: int = 0
    limit_down_count: int = 0
    broken_limit_count: int = 0  # 炸板数
    turnover_amount: float = 0.0  # 成交额（亿）
    risk_pref: float = 0.0  # 风险偏好 0~1
    regime: str = "normal"  # bull/normal/recovery/weak/bear


class MarketFilter:
    """市场环境过滤器。"""

    def __init__(self, config: Optional[dict] = None):
        self.cfg = dict(RIB_CONFIG.get("market_filter", {}))
        if config:
            self.cfg.update(config)

    def evaluate(self, snapshot: Optional[MarketSnapshot] = None,
                 index_df: Optional[pd.DataFrame] = None) -> MarketSnapshot:
        """评估市场环境。"""
        if snapshot is None:
            snapshot = MarketSnapshot()
        if snapshot.regime != "normal" and snapshot.regime != "":
            return snapshot

        # 使用指数数据评估
        if index_df is not None and len(index_df) >= 60:
            closes = index_df["close"].values.astype(float)
            ma20 = np.mean(closes[-20:])
            ma60 = np.mean(closes[-60:])
            snapshot.index_price = closes[-1]
            snapshot.index_change_pct = (closes[-1] - closes[-20]) / closes[-20] * 100
            snapshot.index_ma20_slope = (closes[-1] - ma20) / ma20 if ma20 > 0 else 0
            snapshot.index_ma60_slope = (closes[-1] - ma60) / ma60 if ma60 > 0 else 0

        # 判定市场状态
        snapshot.regime = self._determine_regime(snapshot)
        return snapshot

    def _determine_regime(self, snap: MarketSnapshot) -> str:
        """根据快照判定市场状态。"""
        score = 0
        # 指数趋势
        if snap.index_ma20_slope > 0.02:
            score += 3
        elif snap.index_ma20_slope > 0:
            score += 2
        elif snap.index_ma20_slope < -0.02:
            score -= 2
        else:
            score -= 1

        if snap.index_ma60_slope > 0:
            score += 1
        elif snap.index_ma60_slope < 0:
            score -= 1

        # 涨跌比
        if snap.advance_decline_ratio > 2:
            score += 2
        elif snap.advance_decline_ratio > 1:
            score += 1
        elif snap.advance_decline_ratio < 0.5:
            score -= 2

        # 涨停/跌停
        if snap.limit_up_count > 50 and snap.limit_down_count < 5:
            score += 2
        elif snap.limit_up_count > 20:
            score += 1
        elif snap.limit_down_count > 20:
            score -= 2

        # 判定
        if score >= 5:
            return "bull"
        elif score >= 2:
            return "normal"
        elif score >= 0:
            return "recovery"
        elif score >= -2:
            return "weak"
        else:
            return "bear"

    def can_trade(self, regime: str, grade: str, score: float) -> Tuple[bool, str]:
        """判断当前市场环境下是否允许交易。"""
        rules = self.cfg.get(regime, self.cfg.get("normal", {}))
        allow_states = rules.get("allow_states", ())
        min_score = rules.get("min_score", 70)

        if not allow_states:
            return False, f"市场极弱({regime})，所有信号关闭"

        if score < min_score:
            return False, f"分数不足({score:.0f}<{min_score:.0f})，{regime}市场环境下需要更高分"

        return True, f"{regime}市场环境允许交易"


# ═══════════════════════════════════════════════════════
# 主题增强
# ═══════════════════════════════════════════════════════

@dataclass
class ThemeInfo:
    """主题信息。"""
    industry: str = ""
    industry_rank: float = 0.0  # 行业排名百分位
    industry_up_ratio: float = 0.0  # 行业上涨家数占比
    leader_up: bool = False  # 行业龙头是否上涨
    is_main_theme: bool = False  # 是否主升主题
    is_counter_industry: bool = False  # 是否逆行业


class ThemeFilter:
    """主题增强过滤器。"""

    def __init__(self, config: Optional[dict] = None):
        self.cfg = dict(RIB_CONFIG.get("theme", {}))
        if config:
            self.cfg.update(config)

    def compute_bonus(self, info: ThemeInfo) -> float:
        """计算主题增强/惩罚分数。"""
        bonus = 0.0
        if info.industry_up_ratio > 0.5:
            bonus += self.cfg.get("industry_up_bonus", 5)
        if info.leader_up:
            bonus += self.cfg.get("leader_up_bonus", 3)
        if info.is_main_theme:
            bonus += self.cfg.get("theme_main_bonus", 3)
        if info.is_counter_industry:
            bonus += self.cfg.get("counter_industry_penalty", -5)
        return max(-10.0, min(15.0, bonus))


# ═══════════════════════════════════════════════════════
# 盈亏比引擎
# ═══════════════════════════════════════════════════════

@dataclass
class TradePlan:
    """交易计划。"""
    buy_price: float = 0.0
    stop_loss: float = 0.0
    target1: float = 0.0
    target2: float = 0.0
    risk_reward: float = 0.0
    position_pct: float = 0.30
    holding_days: int = 5
    zone_low: float = 0.0  # 建议买入区下限
    zone_high: float = 0.0  # 建议买入区上限


class RiskRewardEngine:
    """盈亏比计算引擎。"""

    def __init__(self, config: Optional[dict] = None):
        self.cfg = dict(RIB_CONFIG.get("rr", {}))
        if config:
            self.cfg.update(config)

    def compute(self, buy_price: float, atr: float,
                support: float = 0.0) -> TradePlan:
        """计算交易计划。"""
        plan = TradePlan()
        plan.buy_price = buy_price

        sl_atr = self.cfg.get("stop_loss_atr", 1.5)
        t1_atr = self.cfg.get("target1_atr", 3.0)
        t2_atr = self.cfg.get("target2_atr", 5.0)

        # 止损
        stop_distance = sl_atr * atr if atr > 0 else buy_price * 0.05
        plan.stop_loss = max(buy_price - stop_distance, support) if support > 0 else buy_price - stop_distance

        # 目标
        plan.target1 = buy_price + t1_atr * atr if atr > 0 else buy_price * 1.05
        plan.target2 = buy_price + t2_atr * atr if atr > 0 else buy_price * 1.10

        # 盈亏比
        risk = buy_price - plan.stop_loss
        reward = plan.target1 - buy_price
        plan.risk_reward = reward / risk if risk > 0 else 0

        # 建议买入区
        plan.zone_low = buy_price - 0.3 * atr if atr > 0 else buy_price * 0.98
        plan.zone_high = buy_price + 0.2 * atr if atr > 0 else buy_price * 1.02

        plan.position_pct = self.cfg.get("position_size", 0.30)
        plan.holding_days = self.cfg.get("holding_days", 5)

        return plan

    def is_acceptable(self, risk_reward: float) -> bool:
        """盈亏比是否可接受。"""
        return risk_reward >= self.cfg.get("min_rr", 2.0)


# ═══════════════════════════════════════════════════════
# 强制否决检查
# ═══════════════════════════════════════════════════════

class VetoChecker:
    """强制否决检查器。"""

    def __init__(self, config: Optional[dict] = None):
        self.cfg = dict(RIB_CONFIG.get("veto", {}))
        if config:
            self.cfg.update(config)
        self.triggered: List[str] = []

    def check(
        self,
        impulse_return: float,
        impulse_volume_ratio: float,
        trend_changed: bool,
        pullback_depth: float,
        is_volume_plunge: bool,
        is_back_to_origin: bool,
        ma20_down: bool,
        is_fake_breakout: bool,
        fell_back_to_base: bool,
        pullback_volume_high: bool,
        distance_atr: float,
        risk_reward: float,
        market_is_bear: bool,
    ) -> Tuple[bool, List[str]]:
        """检查是否触发强制否决。

        Returns:
            (is_vetoed, reasons)
        """
        self.triggered = []

        if impulse_return < self.cfg.get("min_impulse_return", 0.15):
            self.triggered.append(f"① 第一波上涨不足{self.cfg.get('min_impulse_return', 0.15)*100:.0f}%({impulse_return*100:.1f}%)")

        if impulse_volume_ratio < self.cfg.get("min_impulse_volume_ratio", 1.2):
            self.triggered.append(f"② 第一波无明显量能({impulse_volume_ratio:.2f})")

        if self.cfg.get("require_trend_change", True) and not trend_changed:
            self.triggered.append("③ 第一波未改变下降趋势")

        if pullback_depth > self.cfg.get("max_pullback_depth", 0.70):
            self.triggered.append(f"④ 平台回撤超过{self.cfg.get('max_pullback_depth', 0.70)*100:.0f}%({pullback_depth*100:.1f}%)")

        if self.cfg.get("forbid_volume_plunge", True) and is_volume_plunge:
            self.triggered.append("⑤ 平台放量下跌")

        if self.cfg.get("forbid_back_to_origin", True) and is_back_to_origin:
            self.triggered.append("⑥ 跌回第一波启动区")

        if self.cfg.get("forbid_ma20_down", True) and ma20_down:
            self.triggered.append("⑦ MA20 重新明显向下")

        if self.cfg.get("forbid_fake_breakout", True) and is_fake_breakout:
            self.triggered.append("⑧ 第二波突破是假突破")

        if self.cfg.get("forbid_quick_fall_back", True) and fell_back_to_base:
            self.triggered.append("⑨ 突破后快速跌回平台")

        if self.cfg.get("forbid_pullback_volume", True) and pullback_volume_high:
            self.triggered.append("⑩ 回踩放量")

        if distance_atr > self.cfg.get("max_distance_atr", 2.0):
            self.triggered.append(f"⑪ 距突破位>{self.cfg.get('max_distance_atr', 2.0)}ATR({distance_atr:.1f}ATR)")

        if risk_reward < self.cfg.get("min_risk_reward", 2.0):
            self.triggered.append(f"⑫ 盈亏比<{self.cfg.get('min_risk_reward', 2.0)}({risk_reward:.1f})")

        if market_is_bear:
            self.triggered.append("⑬ 市场极端退潮（BEAR）")

        is_vetoed = len(self.triggered) > 0
        return is_vetoed, self.triggered
