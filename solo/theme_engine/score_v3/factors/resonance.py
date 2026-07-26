"""Resonance Factor — 多维共振评分（加分项）.

只有 ETF趋势 + ETF加速度 + 龙头 + 扩散 同时变强，才判定为真正主线。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from theme_engine.score_v3.config import get_threshold
from theme_engine.score_v3.models import ResonanceResult

logger = logging.getLogger(__name__)


async def calc_resonance(
    theme_code: str,
    trade_date: str,
    etf_trend: float = 0.0,
    etf_accel: float = 0.0,
    leader: float = 0.0,
    breadth: float = 0.0,
    **kwargs,
) -> ResonanceResult:
    """计算共振评分.

    规则：
    - 4项全部达标 → 乘数1.10 (+10%)
    - 3项达标 → 乘数1.05 (+5%)
    - 2项达标 → 乘数1.00 (不变)
    - 1项达标 → 乘数0.95 (-5%)
    - 0项达标 → 乘数0.90 (-10%)

    达标阈值:
    - ETFTrend > resonant_etf_trend (默认70)
    - ETFAccel > resonant_etf_accel (默认70)
    - Leader > resonant_leader (默认70)
    - Breadth > resonant_breadth (默认60)
    """
    await asyncio.sleep(0)

    result = ResonanceResult()

    etf_trend_th = get_threshold("resonance_etf_trend", 70)
    etf_accel_th = get_threshold("resonance_etf_accel", 70)
    leader_th = get_threshold("resonance_leader", 70)
    breadth_th = get_threshold("resonance_breadth", 60)

    # ── 连续评分: 每个维度超出阈值越多，权重越高 ──
    def _partial_score(value: float, threshold: float) -> float:
        """计算单个条件的连续评分 0.0~1.0.
        - value >= threshold: 0.5 + 超出比例×0.5 (0.5~1.0)
        - value < threshold:  max(0, value/threshold×0.5) (0.0~0.5)
        """
        if value >= threshold:
            exceed = (value - threshold) / max(100 - threshold, 1)
            return min(1.0, 0.5 + exceed * 0.5)
        else:
            return max(0.0, min(0.5, value / max(threshold, 1) * 0.5))

    partial_scores = [
        _partial_score(etf_trend, etf_trend_th),
        _partial_score(etf_accel, etf_accel_th),
        _partial_score(leader, leader_th),
        _partial_score(breadth, breadth_th),
    ]

    result.etf_trend_met = etf_trend >= etf_trend_th
    result.etf_accel_met = etf_accel >= etf_accel_th
    result.leader_met = leader >= leader_th
    result.breadth_met = breadth >= breadth_th
    conditions_met = sum([
        result.etf_trend_met,
        result.etf_accel_met,
        result.leader_met,
        result.breadth_met,
    ])
    result.conditions_met = conditions_met

    # 连续共振强度 0~4
    resonance_strength = sum(partial_scores)
    # 乘数 = 0.90 + 连续强度×0.05, 范围 0.90~1.10
    result.multiplier = round(0.90 + resonance_strength * 0.05, 4)
    result.multiplier = max(0.85, min(1.15, result.multiplier))

    # 共振分 = 连续强度映射到0~100
    result.score = min(100.0, resonance_strength / 4.0 * 100)
    result.details = {
        "etf_trend": etf_trend,
        "etf_accel": etf_accel,
        "leader": leader,
        "breadth": breadth,
        "thresholds": {
            "etf_trend": etf_trend_th,
            "etf_accel": etf_accel_th,
            "leader": leader_th,
            "breadth": breadth_th,
        },
        "partial_scores": {
            "etf_trend": round(partial_scores[0], 3),
            "etf_accel": round(partial_scores[1], 3),
            "leader": round(partial_scores[2], 3),
            "breadth": round(partial_scores[3], 3),
        },
        "resonance_strength": round(resonance_strength, 3),
        "conditions_met": conditions_met,
        "multiplier": result.multiplier,
    }

    return result
