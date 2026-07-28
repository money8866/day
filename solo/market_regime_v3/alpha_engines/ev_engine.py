# -*- coding: utf-8 -*-
"""
Expected Value Engine (V6.2)

核心升级：
  1. Adjusted_EV = EV × Confidence — 解决小样本高EV误导
  2. 按 Adjusted_EV 排序而非 EV
  3. Confidence Level (A/B/C/D) 基于样本量
  4. Pattern Type 输出

V6.1 继承：
  - 集成 Confidence 到信号判定，低置信度降级
  - 冷启动阶段使用启发式 EV，平滑过渡
"""

import os
import sys
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from enum import Enum

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class Signal(Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    WAIT = "WAIT"
    AVOID = "AVOID"


def get_confidence_level(n_samples: int) -> str:
    """基于样本量确定置信度等级"""
    if n_samples < 5:
        return 'D'
    elif n_samples < 20:
        return 'C'
    elif n_samples < 50:
        return 'B'
    else:
        return 'A'


@dataclass
class EVResult:
    """期望收益计算结果 — V6.2 含Adjusted EV + Confidence Level"""
    ts_code: str
    name: str = ''
    theme: str = ''
    pattern_type: str = ''

    # 概率
    win_probability: float = 0.5
    loss_probability: float = 0.5

    # 预期收益
    expected_return_5d: float = 0.0
    expected_return_10d: float = 0.0
    expected_return_20d: float = 0.0
    expected_drawdown: float = 0.0

    # 风险收益
    avg_win_return: float = 0.0
    avg_loss_return: float = 0.0
    risk_reward_ratio: float = 0.0

    # EV
    expected_value_10d: float = 0.0
    ev_score: float = 50.0

    # V6.1 Confidence + 冷启动
    confidence: float = 0.0
    cold_start_phase: str = 'data_driven'
    blend_alpha: float = 1.0
    ev_adjusted: float = 0.0

    # ════════════════════════════════════
    # V6.2 Adjusted EV + Confidence Level
    # ════════════════════════════════════
    adjusted_ev: float = 0.0           # Adjusted_EV = EV × Confidence
    confidence_level: str = 'D'        # A/B/C/D
    n_samples: int = 0                 # 样本量（决定Level）

    # 信号
    signal: Signal = Signal.AVOID
    signal_reason: str = ''

    # 排名
    rank: int = 0
    total_candidates: int = 0


@dataclass
class EVEngineResult:
    """期望收益引擎输出"""
    trade_date: str
    results: Dict[str, EVResult] = field(default_factory=dict)
    ranked_list: List[EVResult] = field(default_factory=list)


class EVEngine:
    """期望收益引擎 — Adjusted_EV排序 + Confidence Level"""

    def __init__(self, config: dict):
        cfg = config.get('ev_engine', {})
        self.min_samples = cfg.get('min_samples', 5)
        self.buy_threshold = cfg.get('buy_threshold', 0.03)
        self.wait_threshold = cfg.get('wait_threshold', 0.0)
        self.min_win_prob = cfg.get('min_win_prob', 0.55)
        self.max_drawdown_penalty = cfg.get('max_drawdown_penalty', 0.05)
        self.enabled = cfg.get('enabled', True)

        p_cfg = config.get('pattern_engine', {})
        conf_cfg = p_cfg.get('confidence', {})
        self.conf_buy_threshold = conf_cfg.get('buy_threshold', 0.40)
        self.conf_wait_threshold = conf_cfg.get('wait_threshold', 0.25)

    def evaluate(
        self,
        trade_date: str,
        pattern_matches: Dict,
    ) -> EVEngineResult:
        """基于模式匹配结果计算期望收益

        V6.2: 使用 Adjusted_EV = EV × Confidence 排序
        """
        result = EVEngineResult(trade_date=trade_date)
        ev_list = []

        for code, pm in pattern_matches.items():
            win_prob = pm.win_probability
            loss_prob = 1.0 - win_prob
            avg_win = pm.avg_win_return
            avg_loss = abs(pm.avg_loss_return)

            # Raw EV
            ev_10d = win_prob * avg_win - loss_prob * avg_loss

            # 风险收益比
            rrr = avg_win / max(avg_loss, 0.001)
            ev_score = self._normalize_ev(ev_10d)

            confidence = pm.confidence
            cold_start_phase = pm.cold_start_phase
            n_samples = pm.n_samples

            # V6.2: Adjusted_EV = EV × Confidence
            adjusted_ev = ev_10d * confidence

            # V6.2: Confidence Level
            confidence_level = get_confidence_level(n_samples)

            # V6.1 置信度折扣 (用于信号判定)
            if confidence < 0.5:
                confidence_discount = 0.3 + 0.7 * (confidence / 0.5)
                confidence_discount = max(0.3, min(1.0, confidence_discount))
            else:
                confidence_discount = 1.0
            ev_signal_adj = ev_10d * confidence_discount

            # 信号判定（基于 adjusted_ev）
            signal, reason = self._determine_signal(
                ev_signal_adj, adjusted_ev, win_prob, pm.avg_max_drawdown,
                confidence, cold_start_phase, n_samples,
            )

            ev_r = EVResult(
                ts_code=code,
                name=pm.name,
                theme=pm.theme,
                pattern_type=pm.pattern_type,
                win_probability=win_prob,
                loss_probability=loss_prob,
                expected_return_5d=win_prob * pm.avg_return_5d - loss_prob * abs(pm.avg_loss_return) * 0.5,
                expected_return_10d=ev_10d,
                expected_return_20d=win_prob * pm.avg_return_20d - loss_prob * abs(pm.avg_loss_return) * 2,
                expected_drawdown=pm.avg_max_drawdown,
                avg_win_return=avg_win,
                avg_loss_return=avg_loss,
                risk_reward_ratio=rrr,
                expected_value_10d=ev_10d,
                ev_score=ev_score,
                confidence=confidence,
                cold_start_phase=cold_start_phase,
                blend_alpha=pm.blend_alpha,
                ev_adjusted=ev_signal_adj,
                # V6.2
                adjusted_ev=adjusted_ev,
                confidence_level=confidence_level,
                n_samples=n_samples,
                signal=signal,
                signal_reason=reason,
            )
            ev_list.append(ev_r)
            result.results[code] = ev_r

        # V6.2: 按 Adjusted_EV (EV × Confidence) 排序
        ev_list.sort(key=lambda x: x.adjusted_ev, reverse=True)
        for i, ev_r in enumerate(ev_list):
            ev_r.rank = i + 1
            ev_r.total_candidates = len(ev_list)
        result.ranked_list = ev_list

        return result

    def _normalize_ev(self, ev: float) -> float:
        normalized = (ev + 0.10) / 0.20 * 100
        return max(0, min(100, normalized))

    def _determine_signal(
        self,
        ev_adjusted: float,
        adjusted_ev: float,
        win_prob: float,
        expected_dd: float,
        confidence: float,
        cold_start_phase: str,
        n_samples: int,
    ) -> tuple:
        """V6.2: 信号判定基于 adjusted_ev"""
        dd_penalty = abs(expected_dd) > self.max_drawdown_penalty

        # 冷启动
        if cold_start_phase == 'cold':
            if adjusted_ev >= self.buy_threshold * 1.5 and win_prob >= self.min_win_prob + 0.10 and not dd_penalty:
                return Signal.BUY, f"冷启BUY AdjEV={adjusted_ev:+.2%}"
            elif adjusted_ev >= self.wait_threshold and win_prob >= 0.5:
                return Signal.WAIT, f"冷启WAIT AdjEV={adjusted_ev:+.2%}"
            else:
                return Signal.AVOID, f"冷启AVOID AdjEV={adjusted_ev:+.2%}"

        # 置信度硬门槛
        if confidence < self.conf_wait_threshold:
            return Signal.AVOID, f"Conf={confidence:.2f}<{self.conf_wait_threshold}不可靠"

        # 暖机
        if cold_start_phase == 'warm':
            if confidence < self.conf_buy_threshold:
                if adjusted_ev >= self.buy_threshold * 1.2 and win_prob >= self.min_win_prob and not dd_penalty:
                    return Signal.BUY, f"暖机BUY AdjEV={adjusted_ev:+.2%}"
                elif adjusted_ev >= self.wait_threshold and win_prob >= 0.5:
                    return Signal.WAIT, f"暖机WAIT AdjEV={adjusted_ev:+.2%}"
                else:
                    return Signal.AVOID, f"暖机AVOID AdjEV={adjusted_ev:+.2%}"

        # 标准
        if confidence >= self.conf_buy_threshold and adjusted_ev >= self.buy_threshold and win_prob >= self.min_win_prob and not dd_penalty:
            return Signal.STRONG_BUY, f"高确信 AdjEV={adjusted_ev:+.2%} P={win_prob:.0%}"
        elif adjusted_ev >= self.buy_threshold and win_prob >= self.min_win_prob and not dd_penalty:
            return Signal.BUY, f"BUY AdjEV={adjusted_ev:+.2%}"
        elif adjusted_ev >= self.wait_threshold and win_prob >= 0.5:
            return Signal.WAIT, f"WAIT AdjEV={adjusted_ev:+.2%}"
        else:
            return Signal.AVOID, f"AVOID AdjEV={adjusted_ev:+.2%}"

    def get_top_n(self, result: EVEngineResult, n: int = 5) -> List[EVResult]:
        """获取 TopN（按Adjusted EV排序）"""
        return result.ranked_list[:n]
