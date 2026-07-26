"""ETF Acceleration Factor — 加速度评分.

比趋势更重要：斜率变化、EMA剪刀差、趋势二阶导、量能增速。
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Dict, List, Optional

import numpy as np

from theme_engine.score_v3.config import get_factor_weights, get_norm_range
from theme_engine.score_v3.models import ETFAccelResult

logger = logging.getLogger(__name__)


def normalize(value: float, norm_range: List[float]) -> float:
    lo, hi = norm_range
    if hi == lo:
        return 50.0
    clipped = max(lo, min(hi, value))
    return (clipped - lo) / (hi - lo) * 100.0


async def calc_etf_accel(
    theme_code: str,
    trade_date: str,
    df,
    **kwargs,
) -> ETFAccelResult:
    """计算 ETF 加速度评分.

    Args:
        df: ETF 日线 DataFrame (close, volume, trade_date)
    """
    await asyncio.sleep(0)

    result = ETFAccelResult()
    if df is None or df.empty or "close" not in df.columns:
        return result

    # 确保数据升序（最早的在前）
    if "trade_date" in df.columns:
        dates = df["trade_date"].values
        if len(dates) > 1 and str(dates[0]) > str(dates[-1]):
            df = df.sort_values("trade_date").reset_index(drop=True)

    closes = df["close"].values
    n = len(closes)

    weights = get_factor_weights("etf_accel")
    if not weights:
        return result

    sub_scores: Dict[str, float] = {}

    # 1. Slope5: 5日线性回归斜率 (归一化)
    slope5 = _calc_slope(closes, 5) if n >= 5 else 0.0
    nr = get_norm_range("etf_accel", "slope")
    sub_scores["slope_5"] = normalize(slope5, nr)

    # 2. Slope10
    slope10 = _calc_slope(closes, 10) if n >= 10 else slope5
    sub_scores["slope_10"] = normalize(slope10, nr)

    # 3. Slope20
    slope20 = _calc_slope(closes, 20) if n >= 20 else slope10
    sub_scores["slope_20"] = normalize(slope20, nr)

    # 4. EMA短长差 (EMA12 - EMA26)
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26) if n >= 26 else _ema(closes, max(n, 12))
    ema_diff = ((ema12[-1] / ema26[-1]) - 1) * 100 if ema12 and ema26 else 0.0
    nr4 = get_norm_range("etf_accel", "ema_diff")
    sub_scores["ema_short_long_diff"] = normalize(ema_diff, nr4)

    # 5. 趋势二阶导: slope20 - slope20_prev
    if n >= 40:
        slope20_prev = _calc_slope(closes[:n - 5], 20)
    elif n >= 25:
        slope20_prev = _calc_slope(closes[:n - 5], min(20, n - 5))
    else:
        slope20_prev = 0.0
    second_deriv = slope20 - slope20_prev
    nr5 = get_norm_range("etf_accel", "second_deriv")
    sub_scores["trend_second_deriv"] = normalize(second_deriv, nr5)

    # 6. 最近5天成交额增速
    volume = df.get("volume", df.get("vol", None))
    vol_growth = 0.0
    if volume is not None and len(volume) >= 10:
        vol_5d = np.mean(volume.values[-5:])
        vol_10d = np.mean(volume.values[-10:-5])
        vol_growth = (vol_5d / vol_10d - 1) * 100 if vol_10d > 0 else 0.0
    nr6 = get_norm_range("etf_accel", "volume_growth")
    sub_scores["volume_5d_growth"] = normalize(vol_growth, nr6)

    # 7. 最近5天资金净流入增速 (简化: 用成交额变化近似)
    # 需要 moneyflow 数据时可扩展，先用 amount 近似
    amount = df.get("amount", None)
    money_growth = 0.0
    if amount is not None and len(amount) >= 10:
        amt_5d = np.mean(amount.values[-5:])
        amt_10d = np.mean(amount.values[-10:-5])
        money_growth = (amt_5d / amt_10d - 1) * 100 if amt_10d > 0 else 0.0
    nr7 = get_norm_range("etf_accel", "money_growth")
    sub_scores["money_flow_5d_growth"] = normalize(money_growth, nr7)

    # 加权总分
    total_weight = sum(weights.values())
    score = 0.0
    for key, w in weights.items():
        for sk, sv in sub_scores.items():
            if key.startswith(sk) or sk.startswith(key.rstrip("_weight")):
                score += sv * w
                break

    result.score = score / total_weight if total_weight > 0 else 0.0
    result.slope_5 = round(slope5, 6)
    result.slope_10 = round(slope10, 6)
    result.slope_20 = round(slope20, 6)
    result.ema_short_long_diff = round(ema_diff, 4)
    result.trend_second_deriv = round(second_deriv, 6)
    result.volume_5d_growth = round(vol_growth, 2)
    result.details = {"sub_scores": {k: round(v, 2) for k, v in sub_scores.items()}}

    return result


def _calc_slope(values, period: int) -> float:
    """计算最近 period 天的线性回归斜率 (归一化到日均变化率)."""
    if len(values) < period:
        return 0.0
    y = values[-period:]
    x = np.arange(period)
    slope = np.polyfit(x, y, 1)[0]
    avg = np.mean(y)
    return slope / avg if avg != 0 else 0.0


def _ema(values, period: int):
    if len(values) == 0:
        return []
    multiplier = 2 / (period + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append((v - result[-1]) * multiplier + result[-1])
    return result
