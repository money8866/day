"""ETF Trend Factor — 机构趋势评分.

反映机构的真实趋势判断：
20日/60日收益、EMA位置、MACD方向、创新高次数、回撤、Sharpe。
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Dict, List, Optional

import numpy as np

from theme_engine.score_v3.config import (
    get_factor_weights,
    get_norm_range,
)
from theme_engine.score_v3.models import ETFTrendResult

logger = logging.getLogger(__name__)


def normalize(value: float, norm_range: List[float]) -> float:
    """线性归一化到 0~100，范围外截断."""
    lo, hi = norm_range
    if hi == lo:
        return 50.0
    clipped = max(lo, min(hi, value))
    return (clipped - lo) / (hi - lo) * 100.0


async def calc_etf_trend(
    theme_code: str,
    trade_date: str,
    df,
    **kwargs,
) -> ETFTrendResult:
    """计算 ETF 趋势评分.

    Args:
        df: ETF 日线 DataFrame，需含 close, trade_date, high, low 等列
    """
    await asyncio.sleep(0)

    result = ETFTrendResult()
    if df is None or df.empty or "close" not in df.columns:
        return result

    # 确保数据升序（最早的在前）
    if "trade_date" in df.columns:
        dates = df["trade_date"].values
        if len(dates) > 1 and str(dates[0]) > str(dates[-1]):
            df = df.sort_values("trade_date").reset_index(drop=True)

    closes = df["close"].values
    highs = df["high"].values if "high" in df.columns else closes
    lows = df["low"].values if "low" in df.columns else closes
    n = len(closes)

    weights = get_factor_weights("etf_trend")
    if not weights:
        return result

    sub_scores: Dict[str, float] = {}

    # 1. 20日收益
    r20 = (closes[-1] / closes[-20] - 1) * 100 if n >= 20 else 0.0
    nr = get_norm_range("etf_trend", "return_20d")
    sub_scores["return_20d"] = normalize(r20, nr)

    # 2. 60日收益
    r60 = (closes[-1] / closes[-60] - 1) * 100 if n >= 60 else (closes[-1] / closes[0] - 1) * 100
    nr = get_norm_range("etf_trend", "return_60d")
    sub_scores["return_60d"] = normalize(r60, nr)

    # 3. EMA20 位置
    ema20 = _ema(closes, 20)
    ema20_pos = (closes[-1] / ema20[-1] - 1) * 100 if len(ema20) > 0 else 0.0
    sub_scores["ema_20"] = normalize(ema20_pos, [-3, 5])

    # 4. EMA60 位置
    ema60 = _ema(closes, 60) if n >= 60 else _ema(closes, n)
    ema60_pos = (closes[-1] / ema60[-1] - 1) * 100 if len(ema60) > 0 else 0.0
    sub_scores["ema_60"] = normalize(ema60_pos, [-5, 8])

    # 5. MACD 方向
    macd_val = _macd(closes)
    macd_score = 50.0
    if macd_val is not None:
        if macd_val > 0:
            macd_score = 80.0 + min(20.0, macd_val / 0.1)
        else:
            macd_score = 50.0 + max(-30.0, macd_val / 0.1)
    sub_scores["macd"] = max(0.0, min(100.0, macd_score))

    # 6. 20日创新高次数
    new_high_20d = 0
    if n >= 20:
        for i in range(-19, 0):
            window = closes[max(-20, i - 19):i + 1]
            if len(window) > 0 and closes[i] >= max(window):
                new_high_20d += 1
    nr = get_norm_range("etf_trend", "new_high_20d")
    sub_scores["new_high_20d"] = normalize(float(new_high_20d), nr)

    # 7. 60日创新高次数
    new_high_60d = 0
    if n >= 60:
        for i in range(-59, 0):
            window = closes[max(-60, i - 59):i + 1]
            if len(window) > 0 and closes[i] >= max(window):
                new_high_60d += 1
    nr = get_norm_range("etf_trend", "new_high_60d")
    sub_scores["new_high_60d"] = normalize(float(new_high_60d), nr)

    # 8. 20日最大回撤
    max_dd = 0.0
    if n >= 20:
        peak = closes[-20]
        for c in closes[-20:]:
            if c > peak:
                peak = c
            dd = (peak - c) / peak * 100
            if dd > max_dd:
                max_dd = dd
    nr = get_norm_range("etf_trend", "max_drawdown")
    sub_scores["max_drawdown_20d"] = 100.0 - normalize(max_dd, nr)

    # 9. 20日 Sharpe (简化: 日均收益 / 日收益std * sqrt(252))
    if n >= 21:
        returns_20d = np.diff(closes[-21:]) / closes[-21:-1]
        avg_ret = np.mean(returns_20d)
        std_ret = np.std(returns_20d)
        sharpe = (avg_ret / std_ret * math.sqrt(252)) if std_ret > 0 else 0.0
    else:
        sharpe = 0.0
    nr = get_norm_range("etf_trend", "sharpe")
    sub_scores["sharpe_20d"] = normalize(sharpe, nr)

    # 10. 量能趋势 (20日量均 / 60日量均)
    volume = df.get("volume", df.get("vol", None))
    vol_score = 50.0
    if volume is not None and n >= 60:
        vol_20 = np.mean(volume.values[-20:])
        vol_60 = np.mean(volume.values[-60:])
        vol_ratio = vol_20 / vol_60 if vol_60 > 0 else 1.0
        vol_score = normalize(min(vol_ratio, 2.0), [0.5, 1.5])
    sub_scores["volume_trend"] = vol_score

    # 加权总分
    total_weight = sum(weights.values())
    score = 0.0
    for key, w in weights.items():
        for sk, sv in sub_scores.items():
            if key.startswith(sk) or sk.startswith(key.rstrip("_weight")):
                score += sv * w
                break

    result.score = score / total_weight if total_weight > 0 else 0.0

    # ── 分离趋势方向分与趋势质量分 ──
    dir_keys = {"return_20d", "return_60d", "ema_20", "ema_60", "macd", "new_high_20d", "new_high_60d"}
    quality_keys = {"max_drawdown_20d", "sharpe_20d", "volume_trend"}

    dir_sum = qual_sum = 0.0
    dir_cnt = qual_cnt = 0
    for k, v in sub_scores.items():
        if k in dir_keys:
            dir_sum += v
            dir_cnt += 1
        elif k in quality_keys:
            qual_sum += v
            qual_cnt += 1
    result.trend_direction = round(dir_sum / max(dir_cnt, 1), 1)
    result.trend_quality = round(qual_sum / max(qual_cnt, 1), 1)
    result.return_20d = round(r20, 2)
    result.return_60d = round(r60, 2)
    result.ema_20_pos = round(ema20_pos, 2)
    result.ema_60_pos = round(ema60_pos, 2)
    result.macd_direction = "up" if macd_val and macd_val > 0 else "down" if macd_val and macd_val < 0 else "flat"
    result.new_high_20d_count = new_high_20d
    result.new_high_60d_count = new_high_60d
    result.max_drawdown_20d = round(max_dd, 2)
    result.sharpe_20d = round(sharpe, 4)
    result.details = {"sub_scores": {k: round(v, 2) for k, v in sub_scores.items()}}

    return result


def _ema(values, period: int):
    """指数移动平均."""
    if len(values) == 0:
        return []
    multiplier = 2 / (period + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append((v - result[-1]) * multiplier + result[-1])
    return result


def _macd(closes, fast: int = 12, slow: int = 26, signal: int = 9):
    """计算 MACD，返回 bar 值."""
    if len(closes) < slow + signal:
        return None
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    dif = [f - s for f, s in zip(ema_fast, ema_slow)]
    dea = _ema(dif, signal)
    bar = 2 * (dif[-1] - dea[-1]) if len(dif) > 0 and len(dea) > 0 else 0.0
    return bar
