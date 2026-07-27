"""
ELD V2 筹码评分 (Chip Score)

从筹码分布数据评估成本结构、主力锁定情况和获利盘比例。
"""

from __future__ import annotations

from typing import Any, Optional

from .config import ChipScoreConfig
from .models import ChipScoreResult, CyqData


def score_chip(
    ts_code: str,
    data_source: Any,
    config: Optional[ChipScoreConfig] = None,
) -> ChipScoreResult:
    """计算单只股票的筹码评分。

    从筹码分布(CYQ)数据中提取获利盘比例、平均成本偏差、
    集中度、峰强度、锁仓程度等因子进行评分。

    Args:
        ts_code: 股票代码
        data_source: 数据源对象，需有 get_cyq(ts_code) -> list[CyqData] 方法
        config: 筹码评分配置，默认使用全局配置

    Returns:
        筹码评分结果
    """
    if config is None:
        from .config import get_config

        config = get_config().chip

    logic: list[str] = []

    # ── 获取筹码分布数据 ─────────────────────
    cyq_data: Optional[CyqData] = data_source.get_cyq(ts_code)

    if cyq_data is None:
        logic.append("无筹码分布数据，评分为0")
        return ChipScoreResult(score=0.0, logic=logic)

    latest = cyq_data
    prev = None  # 单次数据无法获取趋势

    # ── 因子计算与评分 ───────────────────────

    # 1. 获利盘比例 — 越高越好（上涨动力强）
    profit_ratio_val = latest.profit_ratio
    if profit_ratio_val >= 0.8:
        profit_ratio_score = 100.0
    elif profit_ratio_val >= 0.6:
        profit_ratio_score = 80.0
    elif profit_ratio_val >= 0.4:
        profit_ratio_score = 60.0
    elif profit_ratio_val >= 0.2:
        profit_ratio_score = 40.0
    elif profit_ratio_val >= 0.1:
        profit_ratio_score = 20.0
    else:
        profit_ratio_score = 10.0
    logic.append(
        f"获利盘比例 {profit_ratio_val:.1%} → {profit_ratio_score}分"
    )

    # 2. 平均成本偏差 — 适中正向为好（偏离太小无动力，太大有回调风险）
    # 需要当前价格来计算，尝试从 other 字段获取，或默认为0
    avg_cost = latest.avg_cost
    # 理想区间: 当前价在均成本上方 5%-30%
    if avg_cost > 0:
        # 由于没有 current_price 传参，我们用 peak_price 近似
        current_price = max(latest.peak_price, avg_cost * 1.01)
        avg_cost_diff = (current_price - avg_cost) / avg_cost
    else:
        avg_cost_diff = 0.0

    if 0.05 <= avg_cost_diff <= 0.30:
        avg_cost_diff_score = 100.0
    elif 0.02 <= avg_cost_diff < 0.05:
        avg_cost_diff_score = 80.0
    elif 0.30 < avg_cost_diff <= 0.50:
        avg_cost_diff_score = 70.0
    elif -0.05 <= avg_cost_diff < 0.02:
        avg_cost_diff_score = 50.0
    elif avg_cost_diff > 0.50:
        avg_cost_diff_score = 30.0
    else:  # 深套
        avg_cost_diff_score = 10.0
    logic.append(
        f"成本偏差 {avg_cost_diff:.2%} → {avg_cost_diff_score}分"
    )

    # 3. 成本集中度 — 越低越好（筹码越集中）
    concentration_val = latest.cost_concentration
    if concentration_val <= 0.10:
        concentration_score = 100.0
    elif concentration_val <= 0.15:
        concentration_score = 85.0
    elif concentration_val <= 0.20:
        concentration_score = 65.0
    elif concentration_val <= 0.30:
        concentration_score = 45.0
    elif concentration_val <= 0.40:
        concentration_score = 25.0
    else:
        concentration_score = 10.0
    logic.append(
        f"成本集中度 {concentration_val:.2f} → {concentration_score}分"
    )

    # 4. 成本峰强度 — 越高支撑越强
    peak_strength_val = latest.peak_strength
    if peak_strength_val >= 0.30:
        peak_strength_score = 100.0
    elif peak_strength_val >= 0.20:
        peak_strength_score = 80.0
    elif peak_strength_val >= 0.15:
        peak_strength_score = 60.0
    elif peak_strength_val >= 0.10:
        peak_strength_score = 40.0
    elif peak_strength_val >= 0.05:
        peak_strength_score = 20.0
    else:
        peak_strength_score = 10.0
    logic.append(
        f"成本峰强度 {peak_strength_val:.2f} → {peak_strength_score}分"
    )

    # 5. 锁仓程度 — 越高越好（机构锁定）
    lockup_ratio_val = latest.lockup_ratio
    if lockup_ratio_val >= 0.50:
        lockup_ratio_score = 100.0
    elif lockup_ratio_val >= 0.40:
        lockup_ratio_score = 85.0
    elif lockup_ratio_val >= 0.30:
        lockup_ratio_score = 65.0
    elif lockup_ratio_val >= 0.20:
        lockup_ratio_score = 45.0
    elif lockup_ratio_val >= 0.10:
        lockup_ratio_score = 25.0
    else:
        lockup_ratio_score = 10.0
    logic.append(
        f"锁仓程度 {lockup_ratio_val:.2f} → {lockup_ratio_score}分"
    )

    # 6. 成本上升速度 — 正向变化表示主力加仓
    cost_rise_speed: float = 0.0
    if prev is not None and prev.avg_cost > 0:
        cost_rise_speed = (avg_cost - prev.avg_cost) / prev.avg_cost
    else:
        cost_rise_speed = 0.0

    if cost_rise_speed > 0.05:
        cost_rise_speed_score = 100.0
    elif cost_rise_speed > 0.02:
        cost_rise_speed_score = 80.0
    elif cost_rise_speed > 0.0:
        cost_rise_speed_score = 60.0
    elif cost_rise_speed > -0.02:
        cost_rise_speed_score = 40.0
    else:
        cost_rise_speed_score = 20.0
    logic.append(
        f"成本上升速度 {cost_rise_speed:.2%} → {cost_rise_speed_score}分"
    )

    # ── 加权求和 ─────────────────────────
    raw_score = (
        profit_ratio_score * config.profit_ratio_weight
        + avg_cost_diff_score * config.avg_cost_diff_weight
        + concentration_score * config.concentration_weight
        + peak_strength_score * config.peak_strength_weight
        + lockup_ratio_score * config.lockup_ratio_weight
        + cost_rise_speed_score * config.cost_rise_speed_weight
    )

    final_score = max(0.0, min(100.0, raw_score))

    logic.append(f"加权得分={raw_score:.2f}, 最终={final_score:.2f}")

    return ChipScoreResult(
        score=round(final_score, 2),
        profit_ratio=round(profit_ratio_val, 4),
        avg_cost_diff_pct=round(avg_cost_diff * 100, 2),
        concentration=round(concentration_val, 4),
        peak_strength=round(peak_strength_val, 4),
        lockup_ratio=round(lockup_ratio_val, 4),
        cost_rise_speed=round(cost_rise_speed, 4),
        logic=logic,
    )
