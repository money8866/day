# -*- coding: utf-8 -*-
"""
RIB V2.0 BUY_READINESS 计算模块（规范§12/§13/§22）

BUY_READINESS 代表"当前距离真正买点还有多远"（0~100）：
  = 30% 结构完整度 + 25% 下一状态接近程度 + 15% 量价条件
    + 10% 支撑质量 + 10% 市场环境 + 10% 风险收益比

按状态设上限（防止高分绕过状态机）：
  DOWNTREND<=30  IMPULSE_PEAK<=50  POST_IMPULSE_BASE<=80
  FIRST_PULLBACK<=90  PULLBACK_SUPPORT<=95  RE_ACCELERATION<=100

附加惩罚：
  - 追涨（Close-ImpulseHigh>1.5ATR）降分
  - 突破信号老化（>5日未回踩）降分
  - STRUCTURE_RISK>=50 禁止升级（READINESS 上限40）

三层交易池：
  NOW  : READINESS>=85 且 状态=PULLBACK_SUPPORT/RE_ACCELERATION/PRIMARY_BUY
  NEXT : READINESS 70~84 且 状态=POST_IMPULSE_BASE/PRE_BREAKOUT/FIRST_PULLBACK/PULLBACK_SUPPORT
  WATCH: READINESS 50~69
  其他 : IGNORE
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

import pandas as pd

from .config import RIB_CONFIG
from .v2_next_state import NextStateInfo, _safe_float

if TYPE_CHECKING:
    from .engine import RIBResult


# 状态 → 结构完整度基准分（状态越深，结构越完整）
_STRUCTURE_BASE = {
    "DOWNTREND": 10,
    "REVERSAL_SETUP": 20,
    "IMPULSE_ACTIVE": 30,
    "IMPULSE_PEAK": 40,
    "POST_IMPULSE_BASE": 55,
    "PRE_BREAKOUT": 60,
    "SECOND_LEG_BREAKOUT": 70,
    "FIRST_PULLBACK": 78,
    "PULLBACK_SUPPORT": 85,
    "RE_ACCELERATION": 95,
    "PRIMARY_BUY": 100,
}


@dataclass
class ReadinessInfo:
    """BUY_READINESS 计算结果。"""
    readiness: float = 0.0
    cap: float = 100.0
    s_structure: float = 0.0
    s_next: float = 0.0
    s_vol_price: float = 0.0
    s_support: float = 0.0
    s_market: float = 0.0
    s_rr: float = 0.0
    penalties: List[str] = field(default_factory=list)


class ReadinessEngine:
    """BUY_READINESS 计算引擎。"""

    def __init__(self, config: Optional[dict] = None):
        self.cfg = dict(RIB_CONFIG)
        if config:
            self.cfg.update(config)
        self.v2 = self.cfg.get("v2", {})

    def compute(self, df: pd.DataFrame, result: RIBResult,
                next_info: NextStateInfo) -> ReadinessInfo:
        """计算 BUY_READINESS。"""
        info = ReadinessInfo()
        state = result.state
        info.cap = float(self.v2.get("readiness_caps", {}).get(state, 100.0))
        w = self.v2.get("readiness_weights", {})

        # ── ① 结构完整度 (30%) ──
        info.s_structure = float(_STRUCTURE_BASE.get(state, 0.0))

        # ── ② 下一状态接近程度 (25%) ──
        info.s_next = next_info.score

        # ── ③ 量价条件 (15%) ──
        vol = _safe_float(df["vol_ratio"].values[-1], 0)
        loc = max(0.0, min(1.0, _safe_float(df["close_loc"].values[-1], 0.5)))
        info.s_vol_price = min(100.0, (min(2.0, vol) / 2.0) * 55 + loc * 45)

        # ── ④ 支撑质量 (10%) ──
        info.s_support = self._support_score(result)

        # ── ⑤ 市场环境 (10%) ──
        info.s_market = float(self.v2.get("regime_scores", {})
                              .get(result.market_regime, 60))

        # ── ⑥ 风险收益比 (10%) ──
        rr = result.risk_reward
        info.s_rr = min(100.0, (rr / 3.0) * 100.0) if rr > 0 else 0.0

        readiness = (
            w.get("structure", 0.30) * info.s_structure +
            w.get("next_state", 0.25) * info.s_next +
            w.get("vol_price", 0.15) * info.s_vol_price +
            w.get("support", 0.10) * info.s_support +
            w.get("market", 0.10) * info.s_market +
            w.get("risk_reward", 0.10) * info.s_rr
        )

        # ── 惩罚：追涨 (§22) ──
        imp = result.impulse
        if imp and imp.impulse_high > 0:
            atr = _safe_float(df["atr20"].values[-1], 0)
            if atr > 0:
                dist = (result.close - imp.impulse_high) / atr
                if dist > self.v2.get("chase_penalty_atr", 1.5):
                    pen = min(self.v2.get("chase_penalty_max", 20.0),
                              (dist - 1.5) * 25.0)
                    readiness -= pen
                    info.penalties.append(f"追高{dist:.1f}ATR，扣{pen:.0f}分")

        # ── 惩罚：突破信号老化 (§19) ──
        bo = result.breakout
        if bo and bo.is_breakout and state not in (
                "FAILED_BREAKOUT", "INVALIDATED"):
            end_idx = len(df) - 1
            days_since = end_idx - bo.breakout_idx
            aged_days = self.v2.get("breakout_aged_days", 5)
            if days_since > aged_days:
                pen = float(self.v2.get("breakout_aged_penalty", 15))
                readiness -= pen
                info.penalties.append(f"突破已{days_since}日未回踩(BREAKOUT_AGED)，扣{pen:.0f}分")

        # ── 限制：STRUCTURE_RISK>=50 禁止升级 (§20) ──
        if result.structure_risk >= self.v2.get("structure_risk_no_upgrade", 50):
            info.cap = min(info.cap, 40.0)
            info.penalties.append("STRUCTURE_RISK≥50，禁止状态升级")

        # 应用状态上限
        info.readiness = round(min(info.cap, max(0.0, readiness)), 1)
        return info

    def _support_score(self, result: "RIBResult") -> float:
        """支撑质量评分（0~100，对应 READINESS 中的 10% 权重）。"""
        state = result.state
        pb = result.pullback
        base = result.base
        bo = result.breakout
        imp = result.impulse
        close = result.close

        # 回踩/承接阶段：支撑是否已被有效测试
        if state in ("PULLBACK_SUPPORT", "FIRST_PULLBACK", "RE_ACCELERATION"):
            s = 0.0
            if pb and pb.support_found:
                s += 40
            if pb and pb.is_test_and_reclaim:
                s += 30
            if pb and not pb.broke_impulse_high:
                s += 20
            if pb and pb.pullback_volume_ratio <= 0.8:
                s += 10
            return min(100.0, s)

        # 平台阶段：平台低点 / 涨幅保留率即支撑
        if state in ("POST_IMPULSE_BASE", "PRE_BREAKOUT", "SECOND_LEG_BREAKOUT"):
            s = 0.0
            if base and base.is_base:
                s += 40
                if base.retain_ratio >= 0.70:
                    s += 30
                elif base.retain_ratio >= 0.60:
                    s += 20
                else:
                    s += 10
            if imp and imp.impulse_high > 0 and close > imp.impulse_high:
                s += 30  # 已站上第一波高点，支撑上移
            return min(100.0, s)

        # 突破后延伸：回踩低点不破关键位
        if bo and bo.is_breakout and pb and pb.is_pullback:
            return 70.0 if pb.support_found else 40.0

        return 40.0  # 中性基准


def assign_tier(state: str, readiness: float,
                structure_risk: float = 0.0,
                risk_reward: float = 0.0,
                market_regime: str = "normal",
                distance_atr: float = 99.0,
                pre_score: float = 0.0,
                pullback_vol_ratio: float = 1.0,
                support_gap_atr: float = 99.0) -> str:
    """V2.1 §24~§28：三层交易池分类。

    NOW（§25，极严）：PRIMARY_BUY 或极高质量 RE_ACCELERATION
      - BUY_READINESS>=85
      - 结构风险<35
      - RiskReward>=2
      - 市场非BEAR

    NEXT（§24，四规则之一即入选）：
      A: PRE_BREAKOUT + DistanceToTrigger<=1.0ATR + PRE_BREAKOUT_SCORE>=75
      B: PULLBACK_SUPPORT + 结构风险<50
      C: FIRST_PULLBACK + 距支撑很近(<=0.5ATR) + 回踩缩量(<=0.8)
      D: RE_ACCELERATION 但尚未完全满足 PRIMARY_BUY（READINESS>=80）

    WATCH（§27）：结构未成熟（DOWNTREND/REVERSAL_SETUP/IMPULSE_ACTIVE/
      IMPULSE_PEAK/早期POST_IMPULSE_BASE/SECOND_LEG_BREAKOUT）
    """
    v2 = RIB_CONFIG.get("v2", {})

    # 终态不进入任何池
    if state in ("FAILED_REVERSAL", "FAILED_BREAKOUT",
                 "FAILED_PULLBACK", "INVALIDATED"):
        return "IGNORE"

    # ── NOW（§25）──
    now_min = v2.get("pool_now_min", 85)
    sr_max = v2.get("now_structure_risk_max", 35)
    if state == "PRIMARY_BUY":
        if readiness >= now_min and structure_risk < sr_max \
                and risk_reward >= 2.0 and market_regime != "bear":
            return "NOW"
        return "NEXT"   # PRIMARY_BUY 但有瑕疵 -> 仍属 NEXT 观察
    if state == "RE_ACCELERATION":
        if (readiness >= now_min and structure_risk < sr_max
                and risk_reward >= 2.0 and market_regime != "bear"):
            return "NOW"
        if readiness >= 80:
            return "NEXT"   # 规则D
        return "WATCH"

    # ── NEXT（§24 A/B/C）──
    if state == "PRE_BREAKOUT":
        # 规则A：距触发<=1.0ATR 且 PRE_BREAKOUT_SCORE>=75
        if distance_atr <= v2.get("next_priority_distance_atr", 1.0) \
                and pre_score >= 75 and structure_risk < 50:
            return "NEXT"
        return "WATCH"

    if state == "PULLBACK_SUPPORT":
        # 规则B：结构风险<50
        if structure_risk < 50 and readiness >= 70:
            return "NEXT"
        return "WATCH"

    if state == "FIRST_PULLBACK":
        # 规则C：距支撑近 + 缩量
        if support_gap_atr <= 0.5 and pullback_vol_ratio <= 0.8 \
                and structure_risk < 50:
            return "NEXT"
        return "WATCH"

    # ── WATCH（§27）──
    if state in ("DOWNTREND", "REVERSAL_SETUP", "IMPULSE_ACTIVE",
                 "IMPULSE_PEAK", "POST_IMPULSE_BASE",
                 "SECOND_LEG_BREAKOUT"):
        return "WATCH" if readiness >= v2.get("pool_watch_min", 50) else "IGNORE"

    return "WATCH" if readiness >= v2.get("pool_watch_min", 50) else "IGNORE"
