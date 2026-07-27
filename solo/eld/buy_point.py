"""
买点状态机 — Buy Point State Machine

分析个股日线数据，基于状态机识别最佳买入时机。
根据均线排列、成交量、ATR位置、alpha方向、筹码确认综合判断。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from .constants import BuyPointState, StarRating, STAR_THRESHOLDS, VOLUME_SURGE_THRESHOLD
from .models import BuyPointResult, ChipScoreResult, DailyPriceData, TrendScoreResult

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 内部阈值
# ──────────────────────────────────────────────
_ANNOUNCEMENT_WINDOW_DAYS: int = 5
_ANNOUNCEMENT_CLOSE_TOLERANCE_PCT: float = 1.5  # 价格在公告收盘价±1.5%以内

_BASE_BUILDING_MIN_DAYS: int = 10
_BASE_BUILDING_MAX_RANGE_PCT: float = 8.0  # 10天内波动范围不超过8%

_VOLUME_SHRINK_THRESHOLD: float = 0.7  # 回踩量缩到均量的70%以下

_ATR_LOW_THRESHOLD: float = 0.3  # ATR处于低档位
_ATR_HIGH_THRESHOLD: float = 0.7  # ATR处于高档位

_ALIGNMENT_M5: int = 5
_ALIGNMENT_M10: int = 10
_ALIGNMENT_M20: int = 20
_ALIGNMENT_M60: int = 60


def _compute_ma(data: list[DailyPriceData], period: int) -> float:
    """计算简单移动平均线"""
    prices = [d.close for d in data[-period:]]
    if not prices:
        return 0.0
    return sum(prices) / len(prices)


def _compute_avg_volume(data: list[DailyPriceData], period: int) -> float:
    """计算平均成交量"""
    vols = [d.vol for d in data[-period:]]
    if not vols:
        return 0.0
    return sum(vols) / len(vols)


def _find_announce_date(daily_data: list[DailyPriceData]) -> Optional[str]:
    """尝试从日线数据中推断公告日期附近的价格"""
    # 实际场景中公告日期通过其他渠道传入
    return None


def _get_star_rating(score: float) -> tuple[StarRating, int]:
    """将分数转为星级"""
    for threshold, star in STAR_THRESHOLDS:
        if score >= threshold:
            return star, _star_to_int(star)
    return StarRating.ZERO, 0


def _star_to_int(star: StarRating) -> int:
    mapping = {
        StarRating.FIVE_STAR: 5,
        StarRating.FOUR_STAR: 4,
        StarRating.THREE_STAR: 3,
        StarRating.TWO_STAR: 2,
        StarRating.ONE_STAR: 1,
        StarRating.ZERO: 0,
    }
    return mapping.get(star, 0)


def _check_ma_alignment(data: list[DailyPriceData]) -> tuple[bool, str]:
    """检查均线排列状态

    Returns:
        (是否多头排列, 排列描述)
    """
    if len(data) < _ALIGNMENT_M60:
        return False, "数据不足"

    ma5 = _compute_ma(data, 5)
    ma10 = _compute_ma(data, 10)
    ma20 = _compute_ma(data, 20)
    ma60 = _compute_ma(data, 60)

    if ma5 > ma10 > ma20 > ma60:
        return True, f"MA5({ma5:.2f})>MA10({ma10:.2f})>MA20({ma20:.2f})>MA60({ma60:.2f})"
    elif ma5 > ma10 > ma20:
        return False, "短中期多头，长期未走好"
    elif ma20 < ma60:
        return False, "长期均线压制"
    else:
        return False, "均线纠结"


def _check_volume_confirmation(
    data: list[DailyPriceData],
    lookback: int = 20,
) -> tuple[bool, str]:
    """检查成交量确认

    当日成交量 > 1.5 × 20日均量

    Returns:
        (是否确认, 描述)
    """
    if len(data) < lookback + 1:
        return False, "数据不足"

    avg_vol = _compute_avg_volume(data[:-1], lookback)
    if avg_vol <= 0:
        return False, "无成交量数据"

    current_vol = data[-1].vol
    ratio = current_vol / avg_vol if avg_vol > 0 else 0.0

    if ratio >= VOLUME_SURGE_THRESHOLD:
        return True, f"放量确认: 当日量/均量={ratio:.2f}"
    elif ratio >= 1.0:
        return True, f"量能正常: 当日量/均量={ratio:.2f}"
    else:
        return False, f"缩量: 当日量/均量={ratio:.2f}"


def _check_atr_position(
    data: list[DailyPriceData],
    trend_result: Optional[TrendScoreResult],
) -> tuple[str, str]:
    """判断 ATR 位置（低档/中档/高档）

    基于 TrendScoreResult 或自行计算。

    Returns:
        (位置标签, 描述)
    """
    if trend_result is not None and trend_result.atr_ratio > 0:
        atr_r = trend_result.atr_ratio
    else:
        # 简单估算
        if len(data) < 20:
            return "unknown", "数据不足"
        closes = [d.close for d in data[-20:]]
        avg_price = sum(closes) / len(closes)
        if avg_price <= 0:
            return "unknown", "价格为零"
        atr = max(closes) - min(closes)
        atr_r = atr / avg_price

    if atr_r <= _ATR_LOW_THRESHOLD:
        return "low", f"ATR 低档({atr_r:.2%})，波动收敛"
    elif atr_r >= _ATR_HIGH_THRESHOLD:
        return "high", f"ATR 高档({atr_r:.2%})，波动放大"
    else:
        return "mid", f"ATR 中档({atr_r:.2%})"


def _check_alpha_direction(
    trend_result: Optional[TrendScoreResult],
) -> tuple[str, str]:
    """判断 Alpha 方向

    Returns:
        (方向标签, 描述)
    """
    if trend_result is None:
        return "unknown", "无趋势数据"

    alpha = trend_result.alpha
    if alpha > 5.0:
        return "positive", f"Alpha 正向({alpha:+.2f})"
    elif alpha < -5.0:
        return "negative", f"Alpha 负向({alpha:+.2f})"
    else:
        return "neutral", f"Alpha 中性({alpha:+.2f})"


def _check_chip_confirmation(
    chip_result: Optional[ChipScoreResult],
) -> tuple[bool, str]:
    """检查筹码确认

    筹码集中、获利盘适中、成本差距不大。

    Returns:
        (是否确认, 描述)
    """
    if chip_result is None:
        return False, "无筹码数据"

    reasons: list[str] = []
    confirmed = True

    if chip_result.concentration < 30.0:
        reasons.append(f"筹码集中({chip_result.concentration:.1f}%)")
    else:
        reasons.append(f"筹码分散({chip_result.concentration:.1f}%)")
        confirmed = False

    if 20.0 <= chip_result.profit_ratio <= 80.0:
        reasons.append(f"获利盘适中({chip_result.profit_ratio:.1f}%)")
    else:
        reasons.append(f"获利盘极端({chip_result.profit_ratio:.1f}%)")
        confirmed = False

    return confirmed, "; ".join(reasons)


def _detect_announcement_state(
    data: list[DailyPriceData],
    announce_date: Optional[str],
) -> tuple[bool, float, list[str]]:
    """检测是否处于公告买点状态

    条件：
    - 在公告后 _ANNOUNCEMENT_WINDOW_DAYS 天内
    - 价格在公告当日收盘价附近

    Returns:
        (是否匹配, 置信度分数0-100, 逻辑)
    """
    logic: list[str] = []

    if announce_date is None:
        return False, 0.0, logic

    # 尝试在日线中匹配公告日期
    target_close: Optional[float] = None
    for bar in data:
        if bar.trade_date == announce_date:
            target_close = bar.close
            break

    if target_close is None:
        logic.append(f"日线中未找到公告日期 {announce_date} 的数据")
        return False, 0.0, logic

    current_close = data[-1].close
    diff_pct = abs(current_close - target_close) / target_close * 100.0

    if diff_pct <= _ANNOUNCEMENT_CLOSE_TOLERANCE_PCT:
        logic.append(
            f"公告后窗口内: 当前价{current_close:.2f}≈公告收盘{target_close:.2f}"
            f"（偏差{diff_pct:.2f}%）"
        )
        # 越靠近公告、价格越接近，分数越高
        days_since = 0
        for i in range(len(data) - 1, -1, -1):
            if data[i].trade_date == announce_date:
                days_since = (len(data) - 1) - i
                break
        score = max(70.0, 100.0 - days_since * 6.0)
        return True, score, logic
    else:
        logic.append(f"价格偏离公告收盘价{diff_pct:.2f}%，超出容忍范围")
        return False, 0.0, logic


def _detect_breakout_state(
    data: list[DailyPriceData],
) -> tuple[bool, float, list[str]]:
    """检测是否处于突破买点状态

    条件：
    - 收盘价突破 MA20 和 MA60
    - 成交量 > 1.5 × 20日均量

    Returns:
        (是否匹配, 置信度分数0-100, 逻辑)
    """
    logic: list[str] = []

    if len(data) < 61:
        return False, 0.0, logic

    ma20 = _compute_ma(data, 20)
    ma60 = _compute_ma(data, 60)
    current_close = data[-1].close

    # 突破 MA20 和 MA60
    if current_close <= ma20 or current_close <= ma60:
        logic.append(f"收盘{current_close:.2f}未同时突破MA20({ma20:.2f})/MA60({ma60:.2f})")
        return False, 0.0, logic

    # 昨日未突破MA20（确认是当天刚突破）
    if len(data) > 21:
        prev_close = data[-2].close
        prev_ma20 = _compute_ma(data[:-1], 20)
        if prev_close > prev_ma20:
            logic.append("此前已在MA20上方，非首次突破")
            return False, 0.0, logic

    # 成交量确认
    vol_confirmed, vol_desc = _check_volume_confirmation(data)
    if not vol_confirmed:
        logic.append(f"成交量不足: {vol_desc}")
        return False, 0.0, logic

    logic.append(f"突破MA20({ma20:.2f})/MA60({ma60:.2f})，{vol_desc}")
    score = 85.0
    return True, score, logic


def _detect_first_pullback_state(
    data: list[DailyPriceData],
) -> tuple[bool, float, list[str]]:
    """检测是否处于首次回踩买点状态

    条件：
    - 前期有过明显突破上涨
    - 当前回踩到 MA10 或 MA20 附近
    - 成交量显著萎缩

    Returns:
        (是否匹配, 置信度分数0-100, 逻辑)
    """
    logic: list[str] = []

    if len(data) < 30:
        return False, 0.0, logic

    ma10 = _compute_ma(data, 10)
    ma20 = _compute_ma(data, 20)
    current_close = data[-1].close

    # 检查均线是否多头排列（前期上涨）
    ma5 = _compute_ma(data, 5)
    if not (ma5 > ma10 > ma20):
        logic.append("均线非多头排列，非回踩形态")
        return False, 0.0, logic

    # 价格回踩到 MA10 或 MA20 附近（±1%）
    near_ma10 = abs(current_close - ma10) / ma10 * 100.0 <= 1.5
    near_ma20 = abs(current_close - ma20) / ma20 * 100.0 <= 1.5

    if not (near_ma10 or near_ma20):
        logic.append(f"价格{current_close:.2f}未接近MA10({ma10:.2f})或MA20({ma20:.2f})")
        return False, 0.0, logic

    # 检查是否为首次回踩（前面20天内价格在均线上方）
    above_ma = 0
    for i in range(-20, -1):
        if len(data) + i >= 0 and data[i].close > _compute_ma(data[:i] if i < 0 else data[:i], 20):
            above_ma += 1
    if above_ma < 15:
        logic.append("前期未充分在MA20上方运行，非首次回踩")
        return False, 0.0, logic

    # 成交量萎缩
    avg_vol = _compute_avg_volume(data[:-1], 20)
    current_vol = data[-1].vol
    vol_ratio = current_vol / avg_vol if avg_vol > 0 else 999

    ma_target = ma10 if near_ma10 else ma20
    if vol_ratio <= _VOLUME_SHRINK_THRESHOLD:
        logic.append(
            f"回踩MA{10 if near_ma10 else 20}({ma_target:.2f})附近，"
            f"缩量({vol_ratio:.2f})确认"
        )
    else:
        logic.append(
            f"回踩MA{10 if near_ma10 else 20}({ma_target:.2f})附近，"
            f"量能未萎缩({vol_ratio:.2f})"
        )
        return False, 0.0, logic

    score = 90.0
    return True, score, logic


def _detect_base_building_state(
    data: list[DailyPriceData],
) -> tuple[bool, float, list[str]]:
    """检测是否处于平台整理买点状态

    条件：
    - 近10天价格在 MA20/MA60 附近窄幅波动
    - 波动范围不超过 _BASE_BUILDING_MAX_RANGE_PCT

    Returns:
        (是否匹配, 置信度分数0-100, 逻辑)
    """
    logic: list[str] = []

    if len(data) < _BASE_BUILDING_MIN_DAYS + 20:
        return False, 0.0, logic

    recent = data[-_BASE_BUILDING_MIN_DAYS:]
    highs = [d.high for d in recent]
    lows = [d.low for d in recent]
    ma20 = _compute_ma(data, 20)
    ma60 = _compute_ma(data, 60)

    # 计算波动范围
    avg_price = sum(d.close for d in recent) / len(recent)
    range_pct = (max(highs) - min(lows)) / avg_price * 100.0

    if range_pct > _BASE_BUILDING_MAX_RANGE_PCT:
        logic.append(f"近10日波动{range_pct:.2f}%，超出平台整理范围")
        return False, 0.0, logic

    # 价格在均线附近
    current_close = data[-1].close
    far_from_ma20 = abs(current_close - ma20) / ma20 * 100.0 > 5.0
    far_from_ma60 = abs(current_close - ma60) / ma60 * 100.0 > 5.0

    if far_from_ma20 and far_from_ma60:
        logic.append(f"价格{current_close:.2f}远离MA20({ma20:.2f})/MA60({ma60:.2f})")
        return False, 0.0, logic

    logic.append(
        f"平台整理: {_BASE_BUILDING_MIN_DAYS}日波动{range_pct:.2f}%，"
        f"在MA20({ma20:.2f})/MA60({ma60:.2f})附近"
    )
    score = 75.0
    return True, score, logic


def _detect_second_breakout_state(
    data: list[DailyPriceData],
) -> tuple[bool, float, list[str]]:
    """检测是否处于二次突破买点状态

    条件：
    - 前期有过平台整理（base_building）
    - 当前突破平台高点
    - 成交量放大

    Returns:
        (是否匹配, 置信度分数0-100, 逻辑)
    """
    logic: list[str] = []

    if len(data) < 35:
        return False, 0.0, logic

    # 找平台高点：取近15天最高价
    base_high = max(d.high for d in data[-15:])
    current_close = data[-1].close
    current_high = data[-1].high

    # 突破平台高点
    if current_high <= base_high and current_close <= base_high:
        logic.append(f"未突破平台高点{base_high:.2f}")
        return False, 0.0, logic

    # 成交量确认
    vol_confirmed, vol_desc = _check_volume_confirmation(data)
    if not vol_confirmed:
        logic.append(f"突破量能不足: {vol_desc}")
        return False, 0.0, logic

    # 确认前期有平台整理（波动收敛）
    old_data = data[:-15] if len(data) > 15 else data
    if len(old_data) >= 10:
        old_recent = old_data[-10:]
        old_range = max(d.high for d in old_recent) - min(d.low for d in old_recent)
        old_avg = sum(d.close for d in old_recent) / len(old_recent)
        if old_avg > 0 and old_range / old_avg * 100.0 > _BASE_BUILDING_MAX_RANGE_PCT:
            logic.append("前期无明显平台整理")
            return False, 0.0, logic

    logic.append(
        f"二次突破平台高点{base_high:.2f}，{vol_desc}"
    )
    score = 95.0
    return True, score, logic


def _detect_trend_state(
    data: list[DailyPriceData],
) -> tuple[bool, float, list[str]]:
    """检测是否处于趋势买点状态

    条件：
    - MA5 > MA10 > MA20 > MA60 多头排列
    - 价格沿MA5稳步上行

    Returns:
        (是否匹配, 置信度分数0-100, 逻辑)
    """
    logic: list[str] = []

    if len(data) < 60:
        return False, 0.0, logic

    aligned, alignment_desc = _check_ma_alignment(data)

    if not aligned:
        logic.append(f"均线非多头排列: {alignment_desc}")
        return False, 0.0, logic

    # 检查是否沿MA5上行（近5天收盘都在MA5上方）
    ma5 = _compute_ma(data, 5)
    for i in range(-5, 0):
        if data[i].close < ma5 * 0.98:  # 允许2%的下穿
            logic.append(f"价格未沿MA5({ma5:.2f})上行")
            return False, 0.0, logic

    logic.append(f"趋势上涨: {alignment_desc}")
    score = 80.0
    return True, score, logic


def analyze_buy_point(
    ts_code: str,
    daily_data: list[DailyPriceData],
    chip_result: Optional[ChipScoreResult] = None,
    trend_result: Optional[TrendScoreResult] = None,
    announce_date: Optional[str] = None,
) -> BuyPointResult:
    """分析个股最佳买点

    使用状态机依次检测各买点状态：
    1. ANNOUNCEMENT - 公告窗口买点
    2. BREAKOUT - 突破买点
    3. FIRST_PULLBACK - 首次回踩买点
    4. BASE_BUILDING - 平台整理买点
    5. SECOND_BREAKOUT - 二次突破买点
    6. TREND - 趋势买点

    Args:
        ts_code: 股票代码
        daily_data: 日线价格数据（从新到旧，最后一个是当天）
        chip_result: 筹码评分结果（可选）
        trend_result: 趋势评分结果（可选）
        announce_date: 公告日期字符串（可选，格式 %Y%m%d）

    Returns:
        BuyPointResult: 买点判断结果
    """
    # 排序确保数据从旧到新
    sorted_data = sorted(daily_data, key=lambda x: x.trade_date)
    all_logic: list[str] = []

    if len(sorted_data) < 20:
        all_logic.append(f"数据不足（{len(sorted_data)}条），无法判断买点")
        return BuyPointResult(
            state=BuyPointState.NONE,
            rating=StarRating.ZERO,
            stars_int=0,
            logic=all_logic,
        )

    # ── 状态机检测（优先级从高到低） ──

    # 1. 公告买点
    matched, score, ann_logic = _detect_announcement_state(sorted_data, announce_date)
    if matched:
        all_logic.extend(ann_logic)
        state = BuyPointState.ANNOUNCEMENT
        all_logic.append(f"匹配公告买点，评分{score:.1f}")
    else:
        all_logic.extend(ann_logic)

        # 2. 突破买点
        matched, score, brk_logic = _detect_breakout_state(sorted_data)
        if matched:
            all_logic.extend(brk_logic)
            state = BuyPointState.BREAKOUT
            all_logic.append(f"匹配突破买点，评分{score:.1f}")
        else:
            all_logic.extend(brk_logic)

            # 3. 首次回踩买点
            matched, score, pbk_logic = _detect_first_pullback_state(sorted_data)
            if matched:
                all_logic.extend(pbk_logic)
                state = BuyPointState.FIRST_PULLBACK
                all_logic.append(f"匹配首次回踩买点，评分{score:.1f}")
            else:
                all_logic.extend(pbk_logic)

                # 4. 平台整理买点
                matched, score, bb_logic = _detect_base_building_state(sorted_data)
                if matched:
                    all_logic.extend(bb_logic)
                    state = BuyPointState.BASE_BUILDING
                    all_logic.append(f"匹配平台整理买点，评分{score:.1f}")
                else:
                    all_logic.extend(bb_logic)

                    # 5. 二次突破买点
                    matched, score, sbrk_logic = _detect_second_breakout_state(sorted_data)
                    if matched:
                        all_logic.extend(sbrk_logic)
                        state = BuyPointState.SECOND_BREAKOUT
                        all_logic.append(f"匹配二次突破买点，评分{score:.1f}")
                    else:
                        all_logic.extend(sbrk_logic)

                        # 6. 趋势买点
                        matched, score, tr_logic = _detect_trend_state(sorted_data)
                        if matched:
                            all_logic.extend(tr_logic)
                            state = BuyPointState.TREND
                            all_logic.append(f"匹配趋势买点，评分{score:.1f}")
                        else:
                            all_logic.extend(tr_logic)
                            state = BuyPointState.NONE
                            score = 0.0
                            all_logic.append("无匹配买点")

    # ── 辅助确认 ──
    volume_confirmed, vol_desc = _check_volume_confirmation(sorted_data)
    atr_position, atr_desc = _check_atr_position(sorted_data, trend_result)
    alpha_dir, alpha_desc = _check_alpha_direction(trend_result)
    chip_confirmed, chip_desc = _check_chip_confirmation(chip_result)

    # 评分调整（辅助确认修正）
    if state != BuyPointState.NONE:
        if volume_confirmed:
            score = min(100.0, score + 5.0)
        if chip_confirmed:
            score = min(100.0, score + 5.0)
        if alpha_dir == "positive":
            score = min(100.0, score + 3.0)
        if atr_position == "low":
            score = min(100.0, score + 2.0)

    # 最终星级
    rating, stars_int = _get_star_rating(score)

    # 均线排列描述
    _, alignment_desc = _check_ma_alignment(sorted_data)

    all_logic.extend([
        f"成交量确认: {vol_desc}",
        f"ATR位置: {atr_desc}",
        f"Alpha方向: {alpha_desc}",
        f"筹码确认: {chip_desc}",
        f"综合评分: {score:.1f}分 → {rating.value}",
    ])

    return BuyPointResult(
        state=state,
        rating=rating,
        stars_int=stars_int,
        ma_alignment=alignment_desc,
        volume_confirmation=volume_confirmed,
        atr_position=atr_position,
        alpha_direction=alpha_dir,
        chip_confirmation=chip_confirmed,
        logic=all_logic,
    )
