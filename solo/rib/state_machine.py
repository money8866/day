# -*- coding: utf-8 -*-
"""
状态机模块

管理 RIB 形态识别的状态流转：
  DOWNTREND → REVERSAL_SETUP → IMPULSE_START → IMPULSE_ACTIVE
  → IMPULSE_PEAK → POST_IMPULSE_BASE → PRE_BREAKOUT → SECOND_LEG_BREAKOUT
  → FIRST_PULLBACK → PULLBACK_SUPPORT → RE_ACCELERATION → PRIMARY_BUY
  → HOLD → EXIT

禁止跳级，任何阶段结构破坏 → INVALIDATED
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional

from .config import (
    STATE_DOWNTREND, STATE_REVERSAL_SETUP, STATE_IMPULSE_START,
    STATE_IMPULSE_ACTIVE, STATE_IMPULSE_PEAK, STATE_POST_IMPULSE_BASE,
    STATE_PRE_BREAKOUT, STATE_SECOND_LEG_BREAKOUT, STATE_FIRST_PULLBACK,
    STATE_PULLBACK_SUPPORT, STATE_RE_ACCELERATION, STATE_PRIMARY_BUY,
    STATE_HOLD, STATE_EXIT, STATE_INVALIDATED, STATE_FAILED_REVERSAL,
    STATE_FAILED_BREAKOUT, STATE_FAILED_PULLBACK, VALID_TRANSITIONS,
)


@dataclass
class TransitionRecord:
    """状态转移记录。"""
    from_state: str
    to_state: str
    timestamp: str = ""
    reason: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class RIBState:
    """RIB 状态常量集合。"""
    DOWNTREND = STATE_DOWNTREND
    REVERSAL_SETUP = STATE_REVERSAL_SETUP
    IMPULSE_START = STATE_IMPULSE_START
    IMPULSE_ACTIVE = STATE_IMPULSE_ACTIVE
    IMPULSE_PEAK = STATE_IMPULSE_PEAK
    POST_IMPULSE_BASE = STATE_POST_IMPULSE_BASE
    PRE_BREAKOUT = STATE_PRE_BREAKOUT
    SECOND_LEG_BREAKOUT = STATE_SECOND_LEG_BREAKOUT
    FIRST_PULLBACK = STATE_FIRST_PULLBACK
    PULLBACK_SUPPORT = STATE_PULLBACK_SUPPORT
    RE_ACCELERATION = STATE_RE_ACCELERATION
    PRIMARY_BUY = STATE_PRIMARY_BUY
    HOLD = STATE_HOLD
    EXIT = STATE_EXIT
    INVALIDATED = STATE_INVALIDATED
    FAILED_REVERSAL = STATE_FAILED_REVERSAL
    FAILED_BREAKOUT = STATE_FAILED_BREAKOUT
    FAILED_PULLBACK = STATE_FAILED_PULLBACK


class StateMachine:
    """RIB 状态机。

    管理单只股票的形态识别状态流转。
    """

    def __init__(self, ts_code: str = "", name: str = ""):
        self.ts_code = ts_code
        self.name = name
        self.current_state = STATE_DOWNTREND
        self.previous_state: Optional[str] = None
        self.transitions: List[TransitionRecord] = []
        self.state_entered_at: Dict[str, str] = {}
        self.reasons: List[str] = []
        self.is_valid = True

    def can_transition(self, target_state: str) -> bool:
        """检查是否允许转移到目标状态。"""
        allowed = VALID_TRANSITIONS.get(self.current_state, [])
        return target_state in allowed

    def transition(self, target_state: str, reason: str = "") -> bool:
        """执行状态转移。

        Returns:
            是否成功转移
        """
        if not self.can_transition(target_state):
            self.reasons.append(
                f"非法转移: {self.current_state} → {target_state}（{reason}）"
            )
            return False

        record = TransitionRecord(
            from_state=self.current_state,
            to_state=target_state,
            reason=reason,
        )
        self.transitions.append(record)
        self.previous_state = self.current_state
        self.current_state = target_state
        self.state_entered_at[target_state] = record.timestamp
        if reason:
            self.reasons.append(f"[{self.current_state}] {reason}")
        return True

    def invalidate(self, reason: str = "") -> None:
        """标记为无效。"""
        self.is_valid = False
        self.transition(STATE_INVALIDATED, reason)

    def fail_reversal(self, reason: str = "") -> None:
        """标记反转失败。"""
        self.is_valid = False
        self.transition(STATE_FAILED_REVERSAL, reason)

    def fail_breakout(self, reason: str = "") -> None:
        """标记突破失败。"""
        self.is_valid = False
        self.transition(STATE_FAILED_BREAKOUT, reason)

    def fail_pullback(self, reason: str = "") -> None:
        """标记回踩失败。"""
        self.is_valid = False
        self.transition(STATE_FAILED_PULLBACK, reason)

    @property
    def is_pristine(self) -> bool:
        """是否处于初始状态。"""
        return self.current_state == STATE_DOWNTREND

    @property
    def is_terminal(self) -> bool:
        """是否处于终态。"""
        return self.current_state in (
            STATE_INVALIDATED, STATE_FAILED_REVERSAL,
            STATE_FAILED_BREAKOUT, STATE_FAILED_PULLBACK,
            STATE_EXIT,
        )

    @property
    def can_buy(self) -> bool:
        """是否可买入。"""
        return self.current_state == STATE_PRIMARY_BUY

    @property
    def state_sequence(self) -> List[str]:
        """返回状态转移序列。"""
        return [r.to_state for r in self.transitions]

    def get_duration_in_state(self, state: str) -> int:
        """返回某状态停留的步骤数。"""
        count = 0
        for r in self.transitions:
            if r.from_state == state:
                count += 1
        return count

    def reset(self) -> None:
        """重置状态机。"""
        self.current_state = STATE_DOWNTREND
        self.previous_state = None
        self.transitions.clear()
        self.state_entered_at.clear()
        self.reasons.clear()
        self.is_valid = True

    def summary(self) -> Dict:
        """状态机摘要。"""
        return {
            "ts_code": self.ts_code,
            "name": self.name,
            "current_state": self.current_state,
            "is_valid": self.is_valid,
            "transition_count": len(self.transitions),
            "state_sequence": self.state_sequence,
            "reasons": self.reasons[-5:] if self.reasons else [],
        }
