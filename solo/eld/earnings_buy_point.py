"""
业绩回踩买点引擎 — Earnings Pullback Buy Engine

专为业绩公告后设计的回踩买点检测。
不替换已有 Buy Point Engine，作为事件专用补充。

判断条件（8项）：
1. 公告时间窗口：5-20个交易日
2. 趋势：close > MA20
3. 回撤：距离公告后高点 < 10%
4. 成交量：缩量，量比 < 0.6
5. 趋势强度：Alpha > 70
6. 筹码：没有明显松动（机构状态非派发）
7. 利好兑现检测：公告前20日涨幅 < 20%
8. 公告后价格反应：公告后未出现持续下跌

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


def _check_next_day_buyable(
    sorted_data: list[DailyPriceData],
    ma20: float,
    alpha: float,
    institution_state: Optional[str],
    is_sell_on_news: bool,
    close_above_ma20: bool,
) -> tuple[bool, str]:
    """检查是否在回调中，且次日是更好的买入时机。

    核心逻辑：
    - 今日下跌（收盘 < 昨收）= 正在回调中，明日可能给出更低买点
    - 趋势完好（Alpha>60, 在MA20上方或附近）
    - 机构状态支持（吸筹/洗盘阶段，回调是洗盘而非出货）
    - 非利好兑现（或利好兑现但回调已充分，跌幅>10%）

    Returns:
        (is_buyable, reason)
    """
    if len(sorted_data) < 2:
        return False, "数据不足无法判断"

    today = sorted_data[-1]
    yesterday = sorted_data[-2]

    # ── 条件1: 今日下跌，正在回调中 ──
    is_down_day = today.close < yesterday.close
    if not is_down_day:
        # 今日未下跌，但如果是小幅上涨后缩量企稳也可以
        pct_change = (today.close - yesterday.close) / yesterday.close * 100
        if pct_change > 1.0:
            return False, f"今日上涨{pct_change:.1f}%，非回调日，不宜追高"

    # ── 条件2: 趋势未破坏 ──
    if not close_above_ma20:
        # 允许小幅跌破MA20（不超过3%）
        pct_below = (ma20 - today.close) / ma20 * 100
        if pct_below > 3:
            return False, f"跌破MA20 {pct_below:.1f}%，支撑已破"
    if alpha < 60:
        return False, f"Alpha={alpha:.1f} < 60，趋势偏弱"

    # ── 条件3: 机构状态支持 ──
    _safe_states = {InstitutionState.ACCUMULATION.value, InstitutionState.WASHING.value}
    if institution_state and institution_state not in _safe_states:
        return False, f"机构状态{institution_state}，非吸筹/洗盘阶段"

    # ── 条件4: 利好兑现处理 ──
    if is_sell_on_news:
        # 利好兑现但回调充分（距近期高点>10%），可关注
        recent_high = max(d.high for d in sorted_data[-20:])
        pullback_from_high = (recent_high - today.close) / recent_high * 100
        if pullback_from_high < 10:
            return False, f"利好兑现且回调不充分（仅{pullback_from_high:.1f}%）"
        return True, f"利好兑现但回调充分（{pullback_from_high:.1f}%），明日可关注企稳信号"

    return True, "回调中，趋势完好，机构吸筹/洗盘阶段，明日是更好的买入时机"


def _compute_ma(data: list[DailyPriceData], period: int) -> float:
    """计算移动平均线。"""
    prices = [d.close for d in data[-period:]]
    return sum(prices) / len(prices) if prices else 0.0


def _compute_volume_ratio(data: list[DailyPriceData], period: int = 20) -> float:
    """计算量比（当日量 / 均量）。"""
    if len(data) < period + 1:
        return 1.0
    current_vol = data[-1].vol
    avg_vol = sum(d.vol for d in data[-(period + 1):-1]) / period
    return current_vol / avg_vol if avg_vol > 0 else 1.0


def _compute_atr(data: list[DailyPriceData], period: int = 14) -> float:
    """计算简单ATR（平均真实波幅）。
    
    简化为近 period 日平均振幅（high-low）/ close 的百分比。
    """
    if len(data) < period + 1:
        return 0.0
    recent = data[-period:]
    ranges = [(d.high - d.low) / d.close * 100.0 for d in recent if d.close > 0]
    return sum(ranges) / len(ranges) if ranges else 0.0


def _find_highest_since_announce(
    data: list[DailyPriceData],
    announce_date: str,
) -> tuple[float, int]:
    """找到公告日至今的最高价。"""
    highest = 0.0
    days_since = 0
    found = False

    for i, bar in enumerate(data):
        if bar.trade_date == announce_date:
            found = True
        if found:
            if bar.high > highest:
                highest = bar.high
            days_since = len(data) - i - 1

    return highest, days_since


def _compute_pre_announce_runup(
    data: list[DailyPriceData],
    announce_date: str,
    lookback_days: int = 20,
) -> float:
    """计算公告前 lookback_days 日的涨幅（用于利好兑现检测）。
    
    Returns:
        涨幅百分比（如 25.0 表示涨了25%）
    """
    # 找到公告日在数据中的位置
    announce_idx = -1
    for i, bar in enumerate(data):
        if bar.trade_date == announce_date:
            announce_idx = i
            break

    if announce_idx < lookback_days:
        return 0.0  # 数据不足

    start_close = data[announce_idx - lookback_days].close
    if start_close <= 0:
        return 0.0

    # 公告前一天的收盘价
    pre_announce_close = data[announce_idx - 1].close
    runup_pct = (pre_announce_close - start_close) / start_close * 100.0
    return round(runup_pct, 2)


def _check_post_announce_decline(
    data: list[DailyPriceData],
    announce_date: str,
    lookback_days: int = 5,
    max_decline_pct: float = -5.0,
) -> tuple[bool, float]:
    """检查公告后是否出现持续下跌（利好兑现出货）。
    
    Returns:
        (is_declining, worst_decline_pct)
    """
    announce_idx = -1
    for i, bar in enumerate(data):
        if bar.trade_date == announce_date:
            announce_idx = i
            break

    if announce_idx < 0 or announce_idx >= len(data) - 1:
        return False, 0.0  # 公告日就是最后一天，无法判断

    # 公告后第1天（排除公告当天，因为公告当天可能已经包含在涨幅中）
    post_data = data[announce_idx + 1:]

    if len(post_data) < 2:
        return False, 0.0

    # 检查最近 lookback_days 天
    recent = post_data[:min(lookback_days, len(post_data))]
    first_close = recent[0].close
    if first_close <= 0:
        return False, 0.0

    worst_decline = 0.0
    for bar in recent:
        decline = (bar.close - first_close) / first_close * 100.0
        if decline < worst_decline:
            worst_decline = decline

    return worst_decline < max_decline_pct, round(worst_decline, 2)


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
    total_conditions = 8  # 公告时间 + 趋势 + 回撤 + 缩量 + Alpha + 机构 + 利好兑现 + 公告后反应
    reasons: list[str] = []
    warnings: list[str] = []
    is_sell_on_news = False

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
        conditions_met += 1  # 放宽

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
    pullback_pct = 0.0
    if highest_since > 0:
        pullback_pct = (highest_since - current_close) / highest_since * 100.0
        within_pullback = pullback_pct < cfg.max_pullback_from_high_pct
        if within_pullback:
            conditions_met += 1
            reasons.append(f"距高点回撤{pullback_pct:.1f}% < {cfg.max_pullback_from_high_pct}%")
        else:
            warnings.append(f"距高点回撤{pullback_pct:.1f}% ≥ {cfg.max_pullback_from_high_pct}%")
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
        conditions_met += 1  # 放宽

    # ── 条件6: 机构状态非派发 ──
    if institution_state:
        not_distribute = institution_state != InstitutionState.DISTRIBUTE.value
        if not_distribute:
            conditions_met += 1
            reasons.append(f"机构状态: {institution_state}（非派发）")
        else:
            warnings.append(f"机构状态: {institution_state}（派发中，回避）")
            reasons.append(f"机构状态: {institution_state}（派发中，回避）")
    else:
        reasons.append("无机构状态数据，检查跳过")
        conditions_met += 1  # 放宽

    # ── 条件7: 利好兑现检测 — 公告前涨幅 ──
    pre_runup = 0.0
    if announce_date:
        pre_runup = _compute_pre_announce_runup(
            sorted_data, announce_date, cfg.pre_announce_runup_days
        )
        result.pre_announce_runup_pct = pre_runup
        if pre_runup <= cfg.pre_announce_runup_threshold:
            conditions_met += 1
            reasons.append(f"公告前{cfg.pre_announce_runup_days}日涨幅{pre_runup:.1f}% ≤ {cfg.pre_announce_runup_threshold}%（无利好兑现风险）")
        else:
            is_sell_on_news = True
            warnings.append(f"公告前涨幅{pre_runup:.1f}% ≥ {cfg.pre_announce_runup_threshold}%，利好兑现风险较高")
            reasons.append(f"公告前涨幅{pre_runup:.1f}% ≥ {cfg.pre_announce_runup_threshold}%，利好兑现风险较高")
    else:
        reasons.append("无公告日期，利好兑现检测跳过")
        conditions_met += 1  # 放宽

    # ── 条件8: 公告后价格反应 ──
    has_declined = False
    post_decline = 0.0
    if announce_date:
        has_declined, post_decline = _check_post_announce_decline(
            sorted_data, announce_date,
            cfg.post_announce_decline_days, cfg.max_post_announce_decline,
        )
        if not has_declined:
            conditions_met += 1
            reasons.append(f"公告后无持续下跌（最差跌幅{post_decline:.1f}%，阈值{cfg.max_post_announce_decline}%）")
        else:
            is_sell_on_news = True
            warnings.append(f"公告后持续下跌（最差{post_decline:.1f}%，利好兑现出货信号）")
            reasons.append(f"公告后持续下跌（最差{post_decline:.1f}%，利好兑现出货信号）")
    else:
        reasons.append("无公告日期，公告后反应检测跳过")
        conditions_met += 1  # 放宽

    all_logic.extend(reasons)
    all_logic.append(f"满足条件: {conditions_met}/{total_conditions}")

    if warnings:
        all_logic.append(f"警告: {'; '.join(warnings)}")

    # ── 参考买入价与止损价 ──
    reference_buy_price = min(ma20, current_close * 0.985)
    atr_pct = _compute_atr(sorted_data)
    stop_loss_price = reference_buy_price * (1 - 2 * atr_pct / 100)
    result.reference_buy_price = round(reference_buy_price, 2)
    result.stop_loss_price = round(stop_loss_price, 2)

    result.is_sell_on_news = is_sell_on_news

    # ── 次日可买性评估 ──
    next_day_buyable, next_day_reason = _check_next_day_buyable(
        sorted_data, ma20, alpha, institution_state, is_sell_on_news, above_ma20,
    )
    result.next_day_buyable = next_day_buyable
    result.next_day_buy_reason = next_day_reason

    # ── 评分与信号 ──
    score = (conditions_met / total_conditions) * 100.0

    # 趋势Alpha兜底：Alpha < floor 时 BUY 降级为 WATCH
    alpha_floor_ok = True
    if trend_result is not None and alpha < cfg.trend_alpha_floor:
        alpha_floor_ok = False

    # 信号决策（优化版）：考虑次日可买性
    if conditions_met >= 6 and alpha_floor_ok:
        # 条件充分 → BUY
        signal = EarningsBuySignal.BUY
        stage = "BUY"
        all_logic.append(f"信号: BUY — {conditions_met}/{total_conditions}条件满足，可建仓")
        all_logic.append(f"参考买入价: {reference_buy_price:.2f}（MA20={ma20:.2f}，收盘×0.985={current_close*0.985:.2f}）")
        all_logic.append(f"止损价: {stop_loss_price:.2f}（基于ATR {atr_pct:.1f}%）")
    elif conditions_met >= 4 and next_day_buyable:
        # 条件基本满足 + 回调中次日可买 → BUY（可操作）
        signal = EarningsBuySignal.BUY
        stage = "BUY"
        all_logic.append(f"信号: BUY — {conditions_met}/{total_conditions}条件满足+回调中，明日是更好的买入时机")
        # 回调中，参考买入价取今日收盘价（更贴近实际）
        ref_price = current_close
        all_logic.append(f"参考买入价: {ref_price:.2f}（今日收盘价，回调买入）")
        all_logic.append(f"止损价: {stop_loss_price:.2f}（基于ATR {atr_pct:.1f}%）")
        all_logic.append(f"回调提示: {next_day_reason}")
    elif conditions_met >= 4:
        # 利好兑现风险降级：没有机构豁免时，WATCH 降为 IGNORE
        if is_sell_on_news and not alpha_floor_ok:
            signal = EarningsBuySignal.IGNORE
            stage = "IGNORE"
            digest = [w for w in warnings if "利好兑现" in w or "持续下跌" in w]
            all_logic.append(f"信号: IGNORE — 利好兑现风险+趋势偏弱，条件{conditions_met}/{total_conditions}")
            if digest:
                all_logic.append(f"原因: {'; '.join(digest)}")
        elif is_sell_on_news:
            signal = EarningsBuySignal.WATCH
            stage = "WATCH"
            digest = [w for w in warnings if "利好兑现" in w or "持续下跌" in w]
            all_logic.append(f"信号: WATCH — 条件{conditions_met}/{total_conditions}，但存在利好兑现风险，需等待缩量企稳确认")
            if digest:
                all_logic.append(f"原因: {'; '.join(digest)}")
            all_logic.append(f"参考买入价: {reference_buy_price:.2f}（MA20={ma20:.2f}，需缩量确认）")
            all_logic.append(f"止损价: {stop_loss_price:.2f}")
        else:
            signal = EarningsBuySignal.WATCH
            stage = "WATCH"
            all_logic.append(f"信号: WATCH — 条件{conditions_met}/{total_conditions}，继续观察")
            all_logic.append(f"参考买入价: {reference_buy_price:.2f}（MA20={ma20:.2f}）")
            all_logic.append(f"止损价: {stop_loss_price:.2f}")
    elif conditions_met >= 3 and next_day_buyable:
        # 条件较少，但次日可买（回调中+机构吸筹/洗盘）
        signal = EarningsBuySignal.BUY
        stage = "BUY"
        all_logic.append(f"信号: BUY — 条件仅{conditions_met}/{total_conditions}，但回调充分+机构{institution_state}，明日可低吸")
        ref_price = current_close
        all_logic.append(f"参考买入价: {ref_price:.2f}（今日收盘价，回调低吸）")
        all_logic.append(f"止损价: {stop_loss_price:.2f}（基于ATR {atr_pct:.1f}%）")
        all_logic.append(f"回调提示: {next_day_reason}")
    else:
        # 机构状态豁免：吸筹/洗盘阶段不低于 WATCH
        _safe_states = {InstitutionState.ACCUMULATION.value, InstitutionState.WASHING.value}
        if institution_state in _safe_states and not is_sell_on_news:
            signal = EarningsBuySignal.WATCH
            stage = "WATCH"
            all_logic.append(f"信号: WATCH — 条件仅{conditions_met}/{total_conditions}，但机构{institution_state}状态给予观察评级")
            all_logic.append(f"参考买入价: {reference_buy_price:.2f}（MA20={ma20:.2f}）")
            all_logic.append(f"止损价: {stop_loss_price:.2f}")
        else:
            signal = EarningsBuySignal.IGNORE
            stage = "IGNORE"
            all_logic.append(f"信号: IGNORE — 关键条件不满足（{conditions_met}/{total_conditions}）")

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