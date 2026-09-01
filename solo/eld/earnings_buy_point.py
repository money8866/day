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
import threading
from typing import Any, Optional

import pandas as pd

from .config import get_config
from .constants import EarningsBuySignal, InstitutionState
from .models import (
    EarningsBuyPointResult,
    DailyPriceData,
    TrendScoreResult,
)
from .utils import get_last_trade_date

logger = logging.getLogger(__name__)

# 市场环境门控缓存（key=交易日, value=大盘是否在MA20下方）
# run_pipeline 用线程池并发调用，必须加锁
_market_gate_cache: dict[str, Optional[bool]] = {}
_market_gate_lock = threading.Lock()


def _get_market_ma20_below(data_source: Any) -> Optional[bool]:
    """计算大盘基准指数（沪深300）收盘是否在 MA20 下方。

    回测结论（2026-01~07, 30万样本）：大盘<MA20 期间买点信号整体负期望，
    故用该开关在弱市整体降级 BUY。结果按交易日缓存，全市场只算一次。

    Returns:
        True=大盘<MA20(弱市), False=大盘≥MA20, None=无法计算
    """
    try:
        end_date = get_last_trade_date()
        with _market_gate_lock:
            if end_date in _market_gate_cache:
                return _market_gate_cache[end_date]

        if not hasattr(data_source, "get_benchmark_daily"):
            return None
        cfg = get_config().earnings_buy_point
        benchmark = data_source.get_benchmark_daily(cfg.market_gate_benchmark)
        if not benchmark or len(benchmark) < 25:
            return None
        closes = [d.close for d in sorted(benchmark, key=lambda x: x.trade_date)]
        ma20 = sum(closes[-20:]) / 20.0
        below = closes[-1] < ma20
        with _market_gate_lock:
            _market_gate_cache[end_date] = below
        return below
    except Exception as exc:
        logger.warning("市场环境门控计算失败: %s", exc)
        return None


def _check_next_day_buyable(
    sorted_data: list[DailyPriceData],
    ma20: float,
    alpha: float,
    institution_state: Optional[str],
    is_sell_on_news: bool,
    close_above_ma20: bool,
    washout_exempt_result: Optional[dict] = None,
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

    # ── 挖坑洗盘豁免（V5）：深挖坑+缩量企稳+机构洗盘时，MA20破位属挖坑末端而非支撑破坏 ──
    if washout_exempt_result is not None:
        return True, f"挖坑洗盘豁免触发，坑底低吸（{washout_exempt_result['reason']}）"

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

    # ── 条件2.5: 乖离控制（追高禁止） ──
    bias = (today.close / ma20 - 1) * 100 if ma20 > 0 else 0.0
    cfg = get_config().earnings_buy_point
    if bias > cfg.bias_chase_threshold:
        return False, f"乖离{bias:.1f}%过大，追高风险，等待回踩MA10/MA20"

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


def _detect_stock_level_pullback(
    sorted_data: list[DailyPriceData],
    ma20: float,
    close_above_ma20: bool,
    institution_state: Optional[str],
    is_sell_on_news: bool,
) -> Optional[dict]:
    """个股级别回踩企稳检测（独立于大盘Alpha）

    从博通集成0804特征提炼：
    - 前期有过上涨（非单边下跌）
    - 近2日缩量企稳（跌不动了）
    - 收盘在MA5/MA20附近（均线支撑）
    - MACD绿柱缩小（动能收敛）
    - 机构状态非派发

    Returns:
        dict with 'score', 'reason' or None
    """
    if len(sorted_data) < 30:
        return None

    today = sorted_data[-1]
    yesterday = sorted_data[-2]

    # ── 条件1: 机构状态非派发 ──
    _safe_states = {InstitutionState.ACCUMULATION.value, InstitutionState.WASHING.value, ""}
    if institution_state and institution_state not in _safe_states:
        return None

    # ── 条件2: 利好兑现的不做（太危险） ──
    if is_sell_on_news:
        return None

    # ── 条件3: 收盘在MA20附近（不超过±5%） ──
    dist_ma20 = abs(today.close / ma20 - 1) * 100
    if dist_ma20 > 5:
        return None

    # ── 条件4: MA20斜率向上（20日均线上行=中期趋势在） ──
    if len(sorted_data) < 25:
        return None
    ma20_5ago = _compute_ma(sorted_data[:-5], 20)
    ma20_slope = (ma20 / ma20_5ago - 1) * 100 if ma20_5ago > 0 else 0
    if ma20_slope < -2:
        return None  # MA20加速下行，不接

    # ── 条件5: 近2日缩量企稳 ──
    # 今日或昨日涨跌幅在[-3.5%, +2.5%]区间（小阴小阳企稳）
    today_chg = (today.close / yesterday.close - 1) * 100
    if today_chg > 2.5 or today_chg < -3.5:
        return None

    # 前一天（T-2）应该是下跌的（回调中）
    if len(sorted_data) >= 3:
        prev_chg = (yesterday.close / sorted_data[-3].close - 1) * 100
        if prev_chg > 1.0:
            return None  # 前一天没跌，不是回踩企稳

    # ── 条件6: 缩量（今日量 < 20日均量×0.9） ──
    vol_ma20 = sum(d.vol for d in sorted_data[-21:-1]) / 20
    vol_ratio = today.vol / vol_ma20 if vol_ma20 > 0 else 1.0
    if vol_ratio > 1.2:
        return None  # 放量不是企稳

    # ── 条件7: MACD绿柱缩小或红柱放大（动能收敛/反转） ──
    closes = [d.close for d in sorted_data[-30:]]
    ema12 = pd.Series(closes).ewm(span=12, adjust=False).mean().values
    ema26 = pd.Series(closes).ewm(span=26, adjust=False).mean().values
    dif = ema12 - ema26
    dea = pd.Series(dif).ewm(span=9, adjust=False).mean().values
    macd_bar = (dif - dea) * 2
    macd_converging = False
    if len(macd_bar) >= 2:
        if macd_bar[-1] < 0 and macd_bar[-1] > macd_bar[-2]:
            macd_converging = True  # 绿柱缩小
        elif macd_bar[-1] > 0 and macd_bar[-1] > macd_bar[-2]:
            macd_converging = True  # 红柱放大
    if not macd_converging:
        return None

    # ── 条件8: 近20日有过上涨（不是单边下跌的票） ──
    if len(sorted_data) >= 21:
        chg_20d = (today.close / sorted_data[-21].close - 1) * 100
        if chg_20d < -15:
            return None  # 20日跌太多，不是回踩是破位

    # ========== 评分（100分制） ==========
    score = 0
    reasons = []

    # MA20斜率 (25分)
    if ma20_slope > 3:
        score += 25
        reasons.append(f"MA20斜率{ma20_slope:+.1f}%（强势上行）")
    elif ma20_slope > 1:
        score += 20
        reasons.append(f"MA20斜率{ma20_slope:+.1f}%（上行）")
    elif ma20_slope > 0:
        score += 15
        reasons.append(f"MA20斜率{ma20_slope:+.1f}%（走平偏上）")
    elif ma20_slope > -2:
        score += 8
        reasons.append(f"MA20斜率{ma20_slope:+.1f}%（走平）")

    # 缩量程度 (25分)
    if vol_ratio < 0.6:
        score += 25
        reasons.append(f"极度缩量(量比{vol_ratio:.2f})")
    elif vol_ratio < 0.8:
        score += 20
        reasons.append(f"显著缩量(量比{vol_ratio:.2f})")
    elif vol_ratio < 0.9:
        score += 15
        reasons.append(f"缩量(量比{vol_ratio:.2f})")
    elif vol_ratio <= 1.0:
        score += 10
        reasons.append(f"量能温和(量比{vol_ratio:.2f})")
    else:
        score += 5
        reasons.append(f"量比{vol_ratio:.2f}")

    # MACD收敛 (20分)
    if macd_bar[-1] < 0 and macd_bar[-1] > macd_bar[-2]:
        diff = macd_bar[-2] - macd_bar[-1]
        if diff > 0.05:
            score += 20
            reasons.append("MACD绿柱快速缩小")
        else:
            score += 15
            reasons.append("MACD绿柱缩小")
    elif macd_bar[-1] > 0 and macd_bar[-1] > macd_bar[-2]:
        score += 20
        reasons.append("MACD红柱放大")

    # 均线支撑 (15分)
    ma5 = _compute_ma(sorted_data, 5)
    if abs(today.close / ma5 - 1) * 100 < 1.5:
        score += 15
        reasons.append(f"收盘紧贴MA5({ma5:.2f})")
    elif dist_ma20 < 2:
        score += 12
        reasons.append(f"收盘在MA20附近(偏差{dist_ma20:.1f}%)")
    elif dist_ma20 < 5:
        score += 8
        reasons.append(f"收盘距MA20 {dist_ma20:.1f}%")

    # 企稳确认 (15分)
    if today_chg >= 0 and today_chg <= 2.5:
        score += 15
        reasons.append(f"今日小阳企稳({today_chg:+.1f}%)")
    elif today_chg >= -1:
        score += 10
        reasons.append(f"今日微跌企稳({today_chg:+.1f}%)")
    else:
        score += 5
        reasons.append(f"今日跌{today_chg:+.1f}%")

    # 机构状态加分 (0-10分，额外)
    if institution_state == InstitutionState.ACCUMULATION.value:
        score = min(100, score + 5)
        reasons.append("机构吸筹")
    elif institution_state == InstitutionState.WASHING.value:
        score = min(100, score + 3)
        reasons.append("机构洗盘")

    return {
        "score": score,
        "reason": "；".join(reasons),
        "vol_ratio": round(vol_ratio, 2),
        "ma20_slope": round(ma20_slope, 2),
        "today_chg": round(today_chg, 2),
        "dist_ma20": round(dist_ma20, 2),
    }


def _detect_washout_dip_exemption(
    sorted_data: list[DailyPriceData],
    institution_state: Optional[str],
    vol_ratio: float,
    has_event: bool,
    cfg: Any,
) -> Optional[dict]:
    """挖坑洗盘豁免检测（V5，中文在线2026-08-31案例提炼）。

    模式：事件驱动（预告/中报）→ 深度挖坑（距30日高点回撤≥15%）→
    连续2日缩量企稳（小阴小阳+量比≤1.0）→ 机构洗盘/吸筹。
    此时跌破MA20属挖坑末端而非"支撑已破"：MA20门控不一票否决，
    参考买入价=现价（坑底低吸），失效价=坑底低点。
    复盘锚点：300364 8/28收盘22.73（破MA20 6.3%）→ 8/31涨停20cm。

    Returns:
        dict: {'retrace_pct','recent_high','pit_low','today_chg','prev_chg','reason'} 或 None
    """
    if not cfg.washout_exempt_enabled or not has_event or len(sorted_data) < 30:
        return None
    _safe_states = {InstitutionState.ACCUMULATION.value, InstitutionState.WASHING.value}
    if institution_state not in _safe_states:
        return None

    today = sorted_data[-1]
    yesterday = sorted_data[-2]

    recent_high = max(d.high for d in sorted_data[-30:])
    retrace = (recent_high - today.close) / recent_high * 100.0 if recent_high > 0 else 0.0
    if retrace < cfg.washout_min_pullback_pct:
        return None

    today_chg = (today.close / yesterday.close - 1) * 100.0
    prev_chg = (yesterday.close / sorted_data[-3].close - 1) * 100.0
    if not (cfg.washout_day_chg_floor <= today_chg <= cfg.washout_day_chg_cap):
        return None
    if not (cfg.washout_day_chg_floor <= prev_chg <= cfg.washout_day_chg_cap):
        return None
    if vol_ratio > cfg.washout_max_vol_ratio:
        return None

    pit_low = min(d.low for d in sorted_data[-10:])
    if today.close > pit_low * (1 + cfg.washout_pit_distance_pct / 100.0):
        return None

    if len(sorted_data) >= 21:
        chg_20d = (today.close / sorted_data[-21].close - 1) * 100.0
        if chg_20d < cfg.washout_max_drop_20d:
            return None

    reason = (
        f"距30日高点{recent_high:.2f}回撤{retrace:.1f}%（挖坑），"
        f"近2日企稳({prev_chg:+.1f}%/{today_chg:+.1f}%)量比{vol_ratio:.2f}，"
        f"机构{institution_state}，坑底低点{pit_low:.2f}"
    )
    return {
        "retrace_pct": round(retrace, 1),
        "recent_high": round(recent_high, 2),
        "pit_low": round(pit_low, 2),
        "today_chg": round(today_chg, 2),
        "prev_chg": round(prev_chg, 2),
        "reason": reason,
    }


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


def _classify_buy_point_type(
    sorted_data: list[DailyPriceData],
    ma5: float,
    ma10: float,
    ma20: float,
    bias_pct: float,
    vol_ratio: float,
    above_ma20: bool,
) -> tuple[str, str]:
    """识别最佳买点类型。

    类型定义（质量从高到低）：
    - VCP_PULLBACK: 缩量回踩MA10/MA20且波动收敛，最佳买点
    - MA20_BOUNCE: 回踩MA20企稳反弹（贴近MA20）
    - MA10_BOUNCE: 回踩MA10企稳（强势浅回踩）
    - BREAKOUT: 放量突破前高，中速买点
    - TREND_FOLLOW: 沿均线多头上行，谨慎追
    - CHASE_HIGH: 乖离过大，追高风险（禁止BUY）

    Returns:
        (type, description)
    """
    if len(sorted_data) < 20:
        return "UNKNOWN", "数据不足"

    today = sorted_data[-1]
    close = today.close

    # 1. 追高风险：乖离>15%
    if bias_pct > 15:
        return "CHASE_HIGH", f"乖离{bias_pct:.1f}%过大，追高风险"

    # 2. 突破：创20日新高 + 放量
    recent_high = max(d.high for d in sorted_data[-20:])
    if close >= recent_high and vol_ratio >= 1.2:
        return "BREAKOUT", f"放量突破20日新高（量比{vol_ratio:.2f}）"

    # 3. 回踩企稳（最佳）：贴近MA10/MA20 + 缩量
    dist_ma10 = abs(close / ma10 - 1) * 100 if ma10 > 0 else 99
    dist_ma20 = abs(close / ma20 - 1) * 100 if ma20 > 0 else 99

    # VCP：贴近MA20(≤3%) 或 贴近MA10(≤2%)，且缩量
    vcp_tight = (dist_ma20 <= 3) or (dist_ma10 <= 2)
    if vcp_tight and vol_ratio <= 0.9:
        if dist_ma20 <= 3:
            return "VCP_PULLBACK", f"缩量回踩MA20企稳（乖离{bias_pct:+.1f}%，量比{vol_ratio:.2f}）"
        return "VCP_PULLBACK", f"缩量回踩MA10企稳（乖离{bias_pct:+.1f}%，量比{vol_ratio:.2f}）"

    # 4. MA20支撑：贴近MA20（≤5%）
    if dist_ma20 <= 5 and above_ma20:
        return "MA20_BOUNCE", f"回踩MA20支撑（乖离{bias_pct:+.1f}%）"

    # 5. MA10支撑：贴近MA10（≤3%）
    if dist_ma10 <= 3 and above_ma20:
        return "MA10_BOUNCE", f"回踩MA10支撑（乖离{bias_pct:+.1f}%）"

    # 6. 趋势跟随
    if above_ma20 and bias_pct > 5:
        return "TREND_FOLLOW", f"沿MA20上行（乖离{bias_pct:+.1f}%），趋势跟随"

    return "UNKNOWN", f"无明确买点（乖离{bias_pct:+.1f}%，距MA10 {dist_ma10:.1f}%，距MA20 {dist_ma20:.1f}%）"


def _compute_buy_quality(
    sorted_data: list[DailyPriceData],
    ma5: float,
    ma10: float,
    ma20: float,
    bias_pct: float,
    vol_ratio: float,
    institution_state: Optional[str],
) -> tuple[float, str]:
    """计算买点质量评分（0-100）。

    维度权重（config）：
    - 乖离合理度 25%：最佳区 -2%~5%，>15%大幅扣分
    - 回踩深度 25%：贴近MA10/MA20（支撑有效性）
    - 缩量程度 20%：量比越低越好
    - 企稳确认 15%：今日涨跌幅小 + MACD绿柱收敛
    - 机构状态 15%：吸筹>洗盘>启动>派发

    Returns:
        (score, reason)
    """
    cfg = get_config().earnings_buy_point
    reasons: list[str] = []
    score = 0.0

    # ── 1. 乖离合理度 (25分) ──
    if cfg.bias_optimal_min <= bias_pct <= cfg.bias_optimal_max:
        score += 25
        reasons.append(f"乖离{bias_pct:+.1f}%（最佳区）")
    elif bias_pct <= cfg.bias_ok_max:
        score += 18
        reasons.append(f"乖离{bias_pct:+.1f}%（可接受）")
    elif bias_pct <= cfg.bias_chase_threshold:
        score += 8
        reasons.append(f"乖离{bias_pct:+.1f}%（偏大）")
    else:
        score += 0
        reasons.append(f"乖离{bias_pct:+.1f}%（追高风险）")

    # ── 2. 回踩深度 (25分)：贴近MA10/MA20 ──
    dist_ma10 = abs(sorted_data[-1].close / ma10 - 1) * 100 if ma10 > 0 else 99
    dist_ma20 = abs(sorted_data[-1].close / ma20 - 1) * 100 if ma20 > 0 else 99
    if dist_ma20 <= 2:
        score += 25
        reasons.append(f"贴近MA20（{dist_ma20:.1f}%）")
    elif dist_ma10 <= 2:
        score += 22
        reasons.append(f"贴近MA10（{dist_ma10:.1f}%）")
    elif dist_ma20 <= 5:
        score += 18
        reasons.append(f"距MA20 {dist_ma20:.1f}%")
    elif dist_ma10 <= 4:
        score += 12
        reasons.append(f"距MA10 {dist_ma10:.1f}%")
    else:
        score += 5
        reasons.append(f"远离均线（MA20偏差{dist_ma20:.1f}%）")

    # ── 3. 缩量程度 (20分) ──
    if vol_ratio < 0.6:
        score += 20
        reasons.append(f"极度缩量（量比{vol_ratio:.2f}）")
    elif vol_ratio < 0.8:
        score += 16
        reasons.append(f"显著缩量（量比{vol_ratio:.2f}）")
    elif vol_ratio < 1.0:
        score += 10
        reasons.append(f"量能温和（量比{vol_ratio:.2f}）")
    else:
        score += 4
        reasons.append(f"放量（量比{vol_ratio:.2f}）")

    # ── 4. 企稳确认 (15分)：小涨小跌 + MACD绿柱收敛 ──
    stabilize = 0
    if len(sorted_data) >= 2:
        today = sorted_data[-1]
        yesterday = sorted_data[-2]
        chg = (today.close / yesterday.close - 1) * 100
        if -3 <= chg <= 3:
            stabilize += 8
            reasons.append(f"今日{chg:+.1f}%（小波动企稳）")
        else:
            reasons.append(f"今日{chg:+.1f}%（波动大）")
        # MACD绿柱收敛
        if len(sorted_data) >= 30:
            closes = [d.close for d in sorted_data[-30:]]
            ema12 = pd.Series(closes).ewm(span=12, adjust=False).mean().values
            ema26 = pd.Series(closes).ewm(span=26, adjust=False).mean().values
            dif = ema12 - ema26
            dea = pd.Series(dif).ewm(span=9, adjust=False).mean().values
            macd_bar = (dif - dea) * 2
            if len(macd_bar) >= 2 and macd_bar[-1] > macd_bar[-2] and macd_bar[-1] < 0:
                stabilize += 7
                reasons.append("MACD绿柱收敛")
    score += stabilize

    # ── 5. 机构状态 (15分) ──
    if institution_state == InstitutionState.ACCUMULATION.value:
        score += 15
        reasons.append("机构吸筹")
    elif institution_state == InstitutionState.WASHING.value:
        score += 12
        reasons.append("机构洗盘")
    elif institution_state == InstitutionState.LAUNCH.value:
        score += 8
        reasons.append("机构启动")
    elif institution_state == InstitutionState.DISTRIBUTE.value:
        score += 0
        reasons.append("机构派发（回避）")
    else:
        score += 6
        reasons.append(f"机构{institution_state or '未知'}")

    return round(min(100.0, score), 1), "；".join(reasons)


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
    """找到公告日至今的最高价，并返回公告后第几个交易日（days_since）。

    修复: 原实现循环结束后 days_since 被最后一根bar覆盖为0，
    导致"公告时间窗口"条件恒不满足、可操作榜5-12日过滤恒为空。
    """
    highest = 0.0
    days_since = 0
    ann_idx = -1

    for i, bar in enumerate(data):
        if bar.trade_date == announce_date:
            ann_idx = i
            break

    if ann_idx >= 0:
        days_since = len(data) - ann_idx - 1
        highest = max((bar.high for bar in data[ann_idx:]), default=0.0)

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


def _compute_report_stage(
    sorted_data: list[DailyPriceData],
    report_date: str,
    cfg: Any,
    announce_date: str = "",
) -> tuple[str, int]:
    """识别"正式报告披露日"前后阶段（石药创新 2026-08 案例提炼）。

    预告已把业绩区间打明牌，正式报告披露日前资金常抢跑（披露后平盘/回落），
    披露后 1-2 日为"利好落地观察期"。规则仅在此阶段对 BUY 降级，避免追高。

    核心猎物判定：ELD 抓的是"预增预告已出 + 正式中报未披露"的窗口期标的。
    当 report_date 早于预告公告日时（fina_indicator 回退返回上一财报期，如
    中报预告 20260727 而 report_date=20260428 为一季报），说明当期中报未
    落地，标记为 "ann_to_report"（预告→报告窗口持有期），不降级、仅高亮。

    Returns:
        (stage, days_to_report)
        stage: "none"(无报告日/不在窗口) / "ann_to_report"(预告已出·报告未披露)
               / "report_pre"(披露前抢跑期,含披露日当日) / "report_post"(披露后落地观察期)
               / "normal"(远离披露日)
        days_to_report: 报告日距当前最后交易日的交易天数（>0=披露前N日, 0=披露当日, <0=已过|N|日）
    """
    if not report_date or len(sorted_data) < 1:
        return "none", 0

    # 报告日早于预告公告日 → 该报告日是上一财报期披露日，当期中报未落地
    if announce_date and announce_date > report_date:
        return "ann_to_report", 0

    report_idx = -1
    for i, bar in enumerate(sorted_data):
        if bar.trade_date == report_date:
            report_idx = i
            break
    if report_idx < 0:
        return "none", 0

    days_to_report = report_idx - (len(sorted_data) - 1)
    if days_to_report >= 0 and days_to_report <= cfg.report_pre_days:
        return "report_pre", days_to_report
    if days_to_report < 0 and -days_to_report <= cfg.report_post_days:
        return "report_post", days_to_report
    return "normal", days_to_report


def detect_earnings_pullback(
    ts_code: str,
    data_source: Any,
    daily_data: list[DailyPriceData],
    announce_date: Optional[str] = None,
    trend_result: Optional[TrendScoreResult] = None,
    institution_state: Optional[str] = None,
    market_ma20_below: Optional[bool] = None,
    report_date: Optional[str] = None,
) -> EarningsBuyPointResult:
    """检测业绩回踩买点。

    Args:
        ts_code: 股票代码。
        data_source: 数据源。
        daily_data: 日线价格数据。
        announce_date: 公告日期 YYYYMMDD。
        trend_result: 趋势评分结果（可选）。
        institution_state: 机构吸筹状态（可选）。
        market_ma20_below: 大盘是否在MA20下方（可选，None时自动计算）。
        report_date: 正式报告披露日 YYYYMMDD（可选，来自 financial.report_date）。
            用于"报告抢跑/落地"阶段识别：披露前3日(含当日)与披露后2日内 BUY 降级 WATCH。

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
    # 注意: trend_result.alpha 是小数收益率（如0.05=5%），需×100转为百分数
    # 才能与 min_alpha（百分制）比较
    alpha = 0.0
    if trend_result is not None:
        alpha = trend_result.alpha * 100.0
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

    # ── 最佳买点信号（V3）：买点类型 + 乖离控制 + 质量评分 ──
    ma5 = _compute_ma(sorted_data, cfg.ma5_period)
    ma10 = _compute_ma(sorted_data, cfg.ma10_period)
    bias_pct = (current_close / ma20 - 1) * 100.0 if ma20 > 0 else 0.0
    result.bias_pct = round(bias_pct, 1)

    buy_point_type, type_desc = _classify_buy_point_type(
        sorted_data, ma5, ma10, ma20, bias_pct, vol_ratio, above_ma20,
    )
    result.buy_point_type = buy_point_type

    quality_score, quality_reason = _compute_buy_quality(
        sorted_data, ma5, ma10, ma20, bias_pct, vol_ratio, institution_state,
    )
    result.buy_quality_score = quality_score
    result.quality_reason = quality_reason
    all_logic.append(f"买点类型: {buy_point_type} — {type_desc}")
    all_logic.append(f"乖离率: {bias_pct:+.1f}% | 买点质量: {quality_score:.0f}/100")
    all_logic.append(f"质量明细: {quality_reason}")

    # ── 参考买入价与止损价（买点类型驱动） ──
    atr_pct = _compute_atr(sorted_data)
    if buy_point_type in ("VCP_PULLBACK", "MA20_BOUNCE"):
        # 回踩MA20支撑，参考买入价=MA20（贴近实际支撑）
        reference_buy_price = ma20
        ref_desc = f"回踩MA20({ma20:.2f})支撑位"
    elif buy_point_type in ("MA10_BOUNCE",):
        reference_buy_price = ma10
        ref_desc = f"回踩MA10({ma10:.2f})支撑位"
    elif buy_point_type == "BREAKOUT":
        # 突破买点：现价附近，略回踩
        reference_buy_price = current_close * 0.99
        ref_desc = f"突破确认位({reference_buy_price:.2f})"
    else:
        # 追高/未知：不给乐观价，用现价×0.985（保守）
        reference_buy_price = current_close * 0.985
        ref_desc = f"保守价({reference_buy_price:.2f})"
    stop_loss_price = reference_buy_price * (1 - 2 * atr_pct / 100)
    result.reference_buy_price = round(reference_buy_price, 2)
    result.stop_loss_price = round(stop_loss_price, 2)

    result.is_sell_on_news = is_sell_on_news

    # ── 挖坑洗盘豁免检测（V5，中文在线2026-08-31案例提炼） ──
    # 事件驱动+深挖坑+连续缩量企稳+机构洗盘 → MA20破位属挖坑末端而非支撑破坏
    washout_exempt_result = _detect_washout_dip_exemption(
        sorted_data, institution_state, vol_ratio,
        has_event=bool(announce_date), cfg=cfg,
    )
    result.washout_exempt = washout_exempt_result is not None
    if washout_exempt_result is not None:
        result.washout_reason = washout_exempt_result["reason"]
        result.washout_pit_low = washout_exempt_result["pit_low"]
        all_logic.append(f"挖坑洗盘豁免: 触发 — {washout_exempt_result['reason']}")
    else:
        all_logic.append("挖坑洗盘豁免: 未触发")

    # ── 次日可买性评估（含乖离控制；豁免触发时绕过MA20/Alpha门控） ──
    next_day_buyable, next_day_reason = _check_next_day_buyable(
        sorted_data, ma20, alpha, institution_state, is_sell_on_news, above_ma20,
        washout_exempt_result=washout_exempt_result,
    )
    result.next_day_buyable = next_day_buyable
    result.next_day_buy_reason = next_day_reason

    # ── 评分与信号 ──
    score = (conditions_met / total_conditions) * 100.0

    # 趋势Alpha兜底：Alpha < floor 时 BUY 降级为 WATCH
    alpha_floor_ok = True
    if trend_result is not None and alpha < cfg.trend_alpha_floor:
        alpha_floor_ok = False

    # ── 个股级别回踩企稳检测（独立于大盘Alpha） ──
    # 当个股形态满足"缩量企稳+均线支撑+MACD收敛"时，不依赖Alpha直接给BUY
    # 适用于大盘bear但个股独立走强的场景（如博通集成0804）
    stock_pullback_buy = _detect_stock_level_pullback(
        sorted_data, ma20, above_ma20, institution_state, is_sell_on_news,
    )
    if stock_pullback_buy is not None:
        stock_pullback_buy["conditions_met"] = conditions_met
        stock_pullback_buy["total_conditions"] = total_conditions
        # 写入结果对象，供CSV/推送使用
        result.stock_pullback_score = stock_pullback_buy["score"]
        result.stock_pullback_reason = stock_pullback_buy["reason"]
    all_logic.append(
        f"个股回踩企稳: {stock_pullback_buy['reason'] if stock_pullback_buy else '未触发'}"
    )

    # ── 信号决策（V3：追高降级 + 质量评分驱动 + 市场环境门控） ──
    # 追高风险判定：乖离>阈值 或 买点类型=CHASE_HIGH
    chase_risk = (bias_pct > cfg.bias_chase_threshold) or (buy_point_type == "CHASE_HIGH")
    quality_buy_ok = quality_score >= cfg.quality_buy_threshold

    # 市场环境门控：大盘<MA20 时 BUY 整体降级 WATCH（回测2026-01~07弱市负期望）
    market_weak = False
    if cfg.market_gate_enabled:
        if market_ma20_below is None:
            market_ma20_below = _get_market_ma20_below(data_source)
        market_weak = bool(market_ma20_below)

    # ── 正式报告阶段识别（石药创新 2026-08 案例：预告→报告窗口经验） ──
    report_stage, days_to_report = _compute_report_stage(
        sorted_data, report_date or "", cfg, announce_date or ""
    )
    result.report_date = report_date or ""
    result.report_stage = report_stage
    result.days_to_report = days_to_report
    if report_stage == "ann_to_report":
        all_logic.append(
            f"正式报告: 中报未披露（预告{announce_date}已出），处于预告→报告窗口持有期，"
            f"事件博弈主战场；报告落地后需防利好兑现"
        )
    elif report_stage == "report_pre":
        all_logic.append(
            f"正式报告: 披露前{days_to_report}个交易日（抢跑期，报告披露后常平盘/回落，追高无意义）"
        )
    elif report_stage == "report_post":
        all_logic.append(
            f"正式报告: 已披露{-days_to_report}个交易日（落地观察期，利好兑现宜等回踩企稳）"
        )

    # 落地观察期内当日大涨追高 → 直接 IGNORE（默认关闭；开启可防石药8/19式涨停次日追高）
    report_panic_ignore = False
    if (
        cfg.report_stage_panic_ignore
        and report_stage == "report_post"
        and len(sorted_data) >= 2
    ):
        day_chg = (
            (sorted_data[-1].close / sorted_data[-2].close - 1) * 100
            if sorted_data[-2].close > 0 else 0.0
        )
        report_panic_ignore = day_chg > 10.0

    if chase_risk:
        # 追高风险 → 绝不BUY，降级WATCH等待回踩
        signal = EarningsBuySignal.WATCH
        stage = "WATCH"
        all_logic.append(
            f"信号: WATCH — 乖离{bias_pct:.1f}%过大（>{cfg.bias_chase_threshold}%），追高风险，"
            f"等待回踩MA10/MA20再介入"
        )
        all_logic.append(f"参考买入价: {reference_buy_price:.2f}（{ref_desc}）")
        all_logic.append(f"止损价: {stop_loss_price:.2f}")
    elif washout_exempt_result is not None:
        # 挖坑洗盘豁免（V5）→ 坑底低吸，失效价=坑底低点（中文在线2026-08-31案例）
        # 豁免检测含6重AND门（事件+深挖坑≥15%+连续2日企稳+量比≤1+坑底6%内+机构洗盘/吸筹），
        # 质量分≥washout_buy_quality_threshold 才给BUY，优先级高于市场弱/报告阶段门控
        if quality_score >= cfg.washout_buy_quality_threshold:
            signal = EarningsBuySignal.BUY
            stage = "BUY"
            all_logic.append(
                f"信号: BUY — 挖坑洗盘豁免触发，坑底低吸（质量{quality_score:.0f}"
                f"≥{cfg.washout_buy_quality_threshold:.0f}，豁免MA20/大盘/报告阶段门控）"
            )
        else:
            signal = EarningsBuySignal.WATCH
            stage = "WATCH"
            all_logic.append(
                f"信号: WATCH — 挖坑洗盘豁免触发，但质量{quality_score:.0f}"
                f"<{cfg.washout_buy_quality_threshold:.0f}，等坑底缩量企稳确认"
            )
        reference_buy_price = current_close
        stop_loss_price = washout_exempt_result["pit_low"]
        result.reference_buy_price = round(reference_buy_price, 2)
        result.stop_loss_price = round(stop_loss_price, 2)
        all_logic.append(f"参考买入价: {reference_buy_price:.2f}（今日收盘价，坑底低吸）")
        all_logic.append(f"止损价: {stop_loss_price:.2f}（坑底低点，跌破即失效）")
        all_logic.append(f"豁免详情: {washout_exempt_result['reason']}")
    elif market_weak:
        # 市场环境弱（大盘<MA20）→ BUY 整体降级 WATCH
        signal = EarningsBuySignal.WATCH
        stage = "WATCH"
        all_logic.append(
            f"信号: WATCH — 市场环境弱（大盘{cfg.market_gate_benchmark} < MA20），"
            f"买点质量{quality_score:.0f}分/乖离{bias_pct:.1f}%也降级，等待大盘企稳"
        )
        all_logic.append(f"参考买入价: {reference_buy_price:.2f}（{ref_desc}）")
        all_logic.append(f"止损价: {stop_loss_price:.2f}")
    elif report_panic_ignore:
        # 正式报告落地观察期内当日大涨>10% → 直接 IGNORE（利好已兑现+短期透支）
        signal = EarningsBuySignal.IGNORE
        stage = "IGNORE"
        all_logic.append(
            f"信号: IGNORE — 正式报告落地观察期内当日大涨{day_chg:.1f}%，"
            f"利好兑现+短期透支，禁止追高"
        )
    elif cfg.report_stage_downgrade and report_stage in ("report_pre", "report_post"):
        # 正式报告抢跑/落地期：BUY 整体降级 WATCH（石药创新 2026-08 经验）
        # 披露前资金抢跑、披露后常平盘/回落，此阶段追高赔率差，等回踩企稳再介入
        signal = EarningsBuySignal.WATCH
        stage = "WATCH"
        if report_stage == "report_pre":
            note = (
                f"信号: WATCH — 正式报告披露前{days_to_report}个交易日（抢跑期），"
                f"报告落地后常平盘/回落，追高无意义，等报告后回踩"
            )
        else:
            note = (
                f"信号: WATCH — 正式报告已披露{-days_to_report}个交易日（落地观察期），"
                f"利好兑现宜等回踩企稳（缩量+贴近MA20）再介入"
            )
        all_logic.append(note)
        all_logic.append(f"参考买入价: {reference_buy_price:.2f}（{ref_desc}）")
        all_logic.append(f"止损价: {stop_loss_price:.2f}")
    elif stock_pullback_buy and not is_sell_on_news:
        # 个股级别回踩企稳触发 → 直接BUY（独立于个股Alpha）
        # 注意：仍受市场环境门控约束（大盘<MA20 时已在上一分支整体降级 WATCH）
        signal = EarningsBuySignal.BUY
        stage = "BUY"
        all_logic.append(
            f"信号: BUY — 个股回踩企稳触发（{stock_pullback_buy['score']:.0f}分），"
            f"独立于大盘Alpha={alpha:.1f}"
        )
        ref_price = current_close
        all_logic.append(f"参考买入价: {ref_price:.2f}（今日收盘价，回踩企稳买入）")
        all_logic.append(f"止损价: {stop_loss_price:.2f}（基于ATR {atr_pct:.1f}%）")
        all_logic.append(f"回调提示: {stock_pullback_buy['reason']}")
    elif quality_buy_ok:
        # 买点质量达标 → BUY（质量分是可操作门槛，须与事件窗口/市场环境组合；回测显示无排序价值）
        signal = EarningsBuySignal.BUY
        stage = "BUY"
        alpha_note = f"（Alpha={alpha:.1f}，形态已含趋势）" if not alpha_floor_ok else ""
        all_logic.append(
            f"信号: BUY — 买点质量{quality_score:.0f}分达标（≥{cfg.quality_buy_threshold:.0f}），"
            f"类型:{buy_point_type}{alpha_note}，条件{conditions_met}/{total_conditions}"
        )
        all_logic.append(f"参考买入价: {reference_buy_price:.2f}（{ref_desc}）")
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
            all_logic.append(f"信号: WATCH — 条件{conditions_met}/{total_conditions}，买点质量{quality_score:.0f}分")
            all_logic.append(f"参考买入价: {reference_buy_price:.2f}（{ref_desc}）")
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
            all_logic.append(f"参考买入价: {reference_buy_price:.2f}（{ref_desc}）")
            all_logic.append(f"止损价: {stop_loss_price:.2f}")
        else:
            signal = EarningsBuySignal.IGNORE
            stage = "IGNORE"
            all_logic.append(f"信号: IGNORE — 关键条件不满足（{conditions_met}/{total_conditions}），买点质量{quality_score:.0f}分")

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