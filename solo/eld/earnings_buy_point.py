"""
业绩回踩买点引擎 — Earnings Pullback Buy Engine

专为业绩公告后设计的回踩买点检测。
不替换已有 Buy Point Engine，作为事件专用补充。

判断条件：
1. 公告时间窗口：5-20个交易日
2. 趋势：close > MA20
3. 回撤：距离公告后高点 < 10%
4. 成交量：缩量，量比 < 0.6
5. 趋势强度：Alpha > 70
6. 筹码：没有明显松动
7. 机构状态：不能是派发(DISTRIBUTE)

输出：
- BUY：全部条件满足
- WATCH：大部分条件满足
- IGNORE：关键条件不满足
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .config import get_config
from .constants import EarningsBuySignal, InstitutionState
from .models import (
    EarningsBuyPointResult,
    DailyPriceData,
    TrendScoreResult,
)

logger = logging.getLogger(__name__)


def _compute_ma(data: list[DailyPriceData], period: int) -> float:
    """计算移动平均线。

    Args:
        data: 日线数据列表。
        period: 周期。

    Returns:
        MA 值。
    """
    prices = [d.close for d in data[-period:]]
    return sum(prices) / len(prices) if prices else 0.0


def _compute_volume_ratio(data: list[DailyPriceData], period: int = 20) -> float:
    """计算量比（当日量 / 均量）。

    Args:
        data: 日线数据列表。
        period: 均量计算周期。

    Returns:
        量比值。
    """
    if len(data) < period + 1:
        return 1.0
    current_vol = data[-1].vol
    avg_vol = sum(d.vol for d in data[-(period + 1):-1]) / period
    return current_vol / avg_vol if avg_vol > 0 else 1.0


def _find_highest_since_announce(
    data: list[DailyPriceData],
    announce_date: str,
) -> tuple[float, int]:
    """找到公告日至今的最高价。

    Args:
        data: 日线数据（从旧到新排序）。
        announce_date: 公告日期 YYYYMMDD。

    Returns:
        (最高价, 距今天数)
    """
    highest = 0.0
    days_since = 0
    found = False

    for i, bar in enumerate(data):
        if bar.trade_date == announce_date:
            found = True
        if found:
            if bar.high > highest:
                highest = bar.high
            days_since = len(data) - i - 1  # 距最后一天的天数

    return highest, days_since


def detect_earnings_pullback(
    ts_code: str,
    data_source: Any,
    daily_data: list[DailyPriceData],
    announce_date: Optional[str] = None,
    trend_result: Optional[TrendScoreResult] = None,
    institution_state: Optional[str] = None,
) -> EarningsBuyPointResult:
    """检测业绩回踩买点。

    Args:
        ts_code: 股票代码。
        data_source: 数据源。
        daily_data: 日线价格数据。
        announce_date: 公告日期 YYYYMMDD。
        trend_result: 趋势评分结果（可选）。
        institution_state: 机构吸筹状态（可选）。

    Returns:
        EarningsBuyPointResult: 业绩回踩买点检测结果。
    """
    cfg = get_config().earnings_buy_point
    result = EarningsBuyPointResult()
    result.ts_code = ts_code
    all_logic: list[str] = []

    # 排序（从旧到新）
    sorted_data = sorted(daily_data, key=lambda x: x.trade_date)

    if len(sorted_data) < cfg.ma_period + 5:
        all_logic.append(f"数据不足（{len(sorted_data)}条），无法判断买点")
        result.signal = EarningsBuySignal.NONE
        result.logic = all_logic
        return result

    conditions_met = 0
    total_conditions = 6  # 公告时间 + 趋势 + 回撤 + 缩量 + Alpha + 机构状态
    reasons: list[str] = []

    # ── 条件1: 公告时间窗口 ──
    days_since = 0
    if announce_date:
        _, days_since = _find_highest_since_announce(sorted_data, announce_date)
        in_window = cfg.min_days_since_announce <= days_since <= cfg.max_days_since_announce
        if in_window:
            conditions_met += 1
            reasons.append(f"公告后{days_since}天（窗口{cfg.min_days_since_announce}-{cfg.max_days_since_announce}天）")
        else:
            reasons.append(f"公告后{days_since}天，不在{cfg.min_days_since_announce}-{cfg.max_days_since_announce}天窗口")
    else:
        reasons.append("无公告日期，跳过窗口检查")
        # 没有公告日期时，放宽条件
        conditions_met += 1

    # ── 条件2: close > MA20 ──
    ma20 = _compute_ma(sorted_data, cfg.ma_period)
    current_close = sorted_data[-1].close
    above_ma20 = current_close > ma20
    if above_ma20:
        conditions_met += 1
        reasons.append(f"收盘{current_close:.2f} > MA20({ma20:.2f})")
    else:
        reasons.append(f"收盘{current_close:.2f} ≤ MA20({ma20:.2f})")

    # ── 条件3: 回撤 < 10% ──
    highest_since, _ = _find_highest_since_announce(sorted_data, announce_date or "")
    if highest_since > 0:
        pullback_pct = (highest_since - current_close) / highest_since * 100.0
        within_pullback = pullback_pct < cfg.max_pullback_from_high_pct
        if within_pullback:
            conditions_met += 1
            reasons.append(f"距高点回撤{pullback_pct:.1f}% < {cfg.max_pullback_from_high_pct}%")
        else:
            reasons.append(f"距高点回撤{pullback_pct:.1f}% ≥ {cfg.max_pullback_from_high_pct}%")
    else:
        reasons.append("无法计算高点回撤")

    # ── 条件4: 缩量，量比 < 0.6 ──
    vol_ratio = _compute_volume_ratio(sorted_data)
    volume_shrunk = vol_ratio < cfg.max_volume_ratio
    if volume_shrunk:
        conditions_met += 1
        reasons.append(f"缩量(量比{vol_ratio:.2f} < {cfg.max_volume_ratio})")
    else:
        reasons.append(f"量能未萎缩(量比{vol_ratio:.2f} ≥ {cfg.max_volume_ratio})")

    # ── 条件5: Alpha > 70 ──
    alpha = 0.0
    if trend_result is not None:
        alpha = trend_result.alpha
        alpha_ok = alpha >= cfg.min_alpha
        if alpha_ok:
            conditions_met += 1
            reasons.append(f"Alpha({alpha:.1f}) ≥ {cfg.min_alpha}")
        else:
            reasons.append(f"Alpha({alpha:.1f}) < {cfg.min_alpha}")
    else:
        reasons.append("无趋势评分数据，Alpha检查跳过")
        conditions_met += 1  # 无数据时放宽

    # ── 条件6: 机构状态非派发 ──
    if institution_state:
        not_distribute = institution_state != InstitutionState.DISTRIBUTE.value
        if not_distribute:
            conditions_met += 1
            reasons.append(f"机构状态: {institution_state}（非派发）")
        else:
            reasons.append(f"机构状态: {institution_state}（派发中，回避）")
    else:
        reasons.append("无机构状态数据，检查跳过")
        conditions_met += 1  # 无数据时放宽

    all_logic.extend(reasons)
    all_logic.append(f"满足条件: {conditions_met}/{total_conditions}")

    # ── 评分与信号 ──
    score = (conditions_met / total_conditions) * 100.0

    if conditions_met >= 5:
        signal = EarningsBuySignal.BUY
        stage = "BUY"
        all_logic.append("信号: BUY — 大部分条件满足，适合建仓")
    elif conditions_met >= 3:
        signal = EarningsBuySignal.WATCH
        stage = "WATCH"
        all_logic.append("信号: WATCH — 部分条件满足，继续观察")
    else:
        # 机构状态豁免：吸筹/洗盘阶段不低于 WATCH（洗盘本身就是潜在买点信号）
        _safe_states = {InstitutionState.ACCUMULATION.value, InstitutionState.WASHING.value}
        if institution_state in _safe_states:
            signal = EarningsBuySignal.WATCH
            stage = "WATCH"
            all_logic.append(f"信号: WATCH — 条件仅满足{conditions_met}/{total_conditions}，但机构{institution_state}状态给予观察评级")
        else:
            signal = EarningsBuySignal.IGNORE
            stage = "IGNORE"
            all_logic.append("信号: IGNORE — 关键条件不满足")

    result.signal = signal
    result.score = round(score, 2)
    result.stage = stage
    result.days_since_announce = days_since
    result.pullback_from_high_pct = round(pullback_pct, 2) if highest_since > 0 else 0.0
    result.volume_ratio = round(vol_ratio, 2)
    result.close_above_ma20 = above_ma20
    result.alpha = round(alpha, 2)
    result.institution_state = institution_state or ""
    result.logic = all_logic

    return result
