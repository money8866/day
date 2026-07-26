"""Lifecycle Factor — 主题生命周期自动判定.

根据 ETF趋势、加速度、扩散度、龙头质量 自动判断:
Birth → Growth → MainUp → Late → Decline

优化:
- MainUp 增加 扩散度>30 + 龙头质量>40 的门槛
- Decline 增加 加速度<20 的辅助条件
- 逆序保护和一致性检查
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from theme_engine.score_v3.config import load_config, get_lifecycle_bonus
from theme_engine.score_v3.models import LifecycleResult, TransitionResult
from theme_engine.score_v3.factors.transition_detector import detect_transition

logger = logging.getLogger(__name__)


_STAGES = ["birth", "growth", "main_up", "late", "decline"]
_STAGE_ORDER = {s: i for i, s in enumerate(_STAGES)}


async def calc_lifecycle(
    theme_code: str,
    trade_date: str,
    etf_trend_score: float,
    etf_accel_score: float,
    breadth_score: float,
    leader_score: float,
    prev_stage: Optional[str] = None,
    theme_name: str = "",
    money: float = 50.0,
    leader_expand: float = 0.0,
    market_regime: str = "",
    days_in_stage: int = 0,
    **kwargs,
) -> LifecycleResult:
    """自动判定生命周期阶段.

    规则 (优化版 V2):
    1. MainUp: ETF趋势>60 AND 加速度>50 AND 扩散度>30 AND 龙头质量>40
    2. Late: ETF趋势>55 AND 加速度<-20
    3. Growth: ETF趋势>45
    4. Decline: ETF趋势<20 OR (加速度<15 AND 扩散度<20) — 双重确认
    5. Birth: ETF趋势>=25 或介于20~25之间非下降 — 弱起步

    Note: birth阈值(25)和decline阈值(20)之间保留5点缓冲区,
    避免趋势<30的主题全被判为decline.
    """
    await asyncio.sleep(0)

    result = LifecycleResult(
        etf_trend_score=etf_trend_score,
        etf_accel_score=etf_accel_score,
        breadth_score=breadth_score,
        leader_score=leader_score,
    )

    cfg = load_config().get("lifecycle", {})

    birth_etf_th = cfg.get("birth_etf_threshold", 25)
    growth_etf_th = cfg.get("growth_etf_threshold", 45)
    main_up_etf_th = cfg.get("main_up_etf_threshold", 60)
    main_up_accel_th = cfg.get("main_up_accel_threshold", 50)
    main_up_breadth_th = cfg.get("main_up_breadth_threshold", 30)
    main_up_leader_th = cfg.get("main_up_leader_threshold", 40)
    late_etf_th = cfg.get("late_etf_threshold", 55)
    late_accel_th = cfg.get("late_accel_threshold", -20)
    decline_etf_th = cfg.get("decline_etf_threshold", 20)

    # 判定阶段 (优化版 V2)
    # 1. MainUp: 趋势强 + 加速度好 + 扩散度配合 + 龙头质量配合
    if (etf_trend_score >= main_up_etf_th
            and etf_accel_score >= main_up_accel_th
            and breadth_score >= main_up_breadth_th
            and leader_score >= main_up_leader_th):
        stage = "main_up"
    # 2. Late: 趋势尚可但加速度转负
    elif etf_trend_score >= late_etf_th and etf_accel_score <= late_accel_th:
        stage = "late"
    # 3. Growth: 趋势中等
    elif etf_trend_score >= growth_etf_th:
        stage = "growth"
    # 4. Decline: 趋势极弱 或 (加速下滑+扩散差双重确认)
    elif etf_trend_score < decline_etf_th or (etf_accel_score < 15 and breadth_score < 20):
        stage = "decline"
    # 5. Birth: 趋势偏弱但未到衰退 (含20~25缓冲区)
    elif etf_trend_score >= birth_etf_th:
        stage = "birth"
    # 6. 缓冲区 (trend 20~25): 弱起步, 判为birth
    else:
        stage = "birth"

    # 逆序禁止
    if prev_stage and prev_stage in _STAGE_ORDER and stage in _STAGE_ORDER:
        prev_idx = _STAGE_ORDER[prev_stage]
        cur_idx = _STAGE_ORDER[stage]
        if cur_idx < prev_idx:
            stage = prev_stage

    # 加分
    bonus = get_lifecycle_bonus(stage)

    result.stage = stage
    result.stage_bonus = bonus
    result.details = {
        "prev_stage": prev_stage,
        "etf_trend": etf_trend_score,
        "etf_accel": etf_accel_score,
        "breadth": breadth_score,
        "leader": leader_score,
    }

    # ── 迁移检测 V2 ──
    # 计算当前阶段已持续天数
    actual_days = days_in_stage
    if prev_stage and prev_stage != stage:
        actual_days = 1  # 阶段变化, 重置计数
    elif prev_stage and prev_stage == stage:
        actual_days = days_in_stage  # 阶段不变, 累加计数

    result.transition = detect_transition(
        theme_code=theme_code,
        theme_name=theme_name,
        current_stage=stage,
        etf_trend=etf_trend_score,
        etf_accel=etf_accel_score,
        breadth=breadth_score,
        leader=leader_score,
        prev_stage=prev_stage,
        money=money,
        leader_expand=leader_expand,
        market_regime=market_regime,
        days_in_stage=actual_days,
    )

    return result
