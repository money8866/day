"""MarketVolatilityFactor — 市场波动率评分."""

from __future__ import annotations

import logging
from typing import Dict, Tuple

import numpy as np
import pandas as pd

from ..config import get_factor_weights, get_norm_range, load_config
from ..data import MarketDataFetcher
from ..models import MarketVolatilityResult

logger = logging.getLogger(__name__)


def normalize(value: float, norm_range) -> float:
    """将值归一化到 0-100 区间."""
    lo, hi = norm_range
    if hi == lo:
        return 50.0
    clipped = max(lo, min(hi, value))
    return (clipped - lo) / (hi - lo) * 100.0


def _load_index_weights() -> Dict[str, float]:
    """从配置加载指数权重."""
    cfg = load_config()
    return {k: float(v) for k, v in cfg.get("index_weights", {}).items()}


def _compute_atr_ratio(df: pd.DataFrame) -> float:
    """计算 ATR / 价格 比值."""
    close = df["close"].dropna()
    high = df["high"].dropna()
    low = df["low"].dropna()
    if len(close) < 20:
        return 0.01

    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    return atr / close.iloc[-1] if close.iloc[-1] != 0 else 0.01


def _compute_amplitude(df: pd.DataFrame, days: int = 20) -> float:
    """计算日均振幅百分比."""
    if df.empty or "high" not in df.columns or "low" not in df.columns:
        return 0.5
    close = df["close"].dropna()
    if len(close) < days:
        return 0.5
    recent = df.iloc[-days:]
    amplitudes = (recent["high"] - recent["low"]) / recent["close"].shift(1) * 100.0
    return float(amplitudes.mean())


def _compute_volatility_percentile(df: pd.DataFrame) -> float:
    """计算波动率在近60日的百分位."""
    close = df["close"].dropna()
    if len(close) < 30:
        return 0.5
    returns = close.pct_change().dropna()
    window = min(60, len(returns) // 2)
    current_vol = returns.iloc[-window:].std()
    hist_vol = returns.rolling(window).std().dropna()
    if len(hist_vol) < 2:
        return 0.5
    count_below = (hist_vol <= current_vol).sum()
    return count_below / len(hist_vol)


def _compute_vix_equivalent(df: pd.DataFrame) -> float:
    """简化VIX等效指标（20日历史波动率年化）. """
    close = df["close"].dropna()
    if len(close) < 20:
        return 15.0
    returns = close.pct_change().dropna()
    hist_vol_20d = returns.iloc[-20:].std()
    # 年化 = 日波动率 * sqrt(242)
    annualized = hist_vol_20d * np.sqrt(242) * 100.0
    return float(annualized)


def _safe_weighted_avg(vals: list) -> float:
    """带权均值，空列表返回 0."""
    if not vals:
        return 0.0
    total_w = sum(w for _, w in vals)
    return sum(v * w for v, w in vals) / total_w if total_w else 0.0


async def calc_market_volatility(
    fetcher: MarketDataFetcher, trade_date: str
) -> MarketVolatilityResult:
    """计算市场波动率评分."""
    indices = await fetcher.get_all_indices(trade_date)
    if not indices:
        logger.warning("无指数数据，返回默认波动率评分 0")
        return MarketVolatilityResult(score=0.0, details={"error": "no data"})

    index_weights = _load_index_weights()

    atr_vals, amp_vals, vola_pct_vals, vix_vals = [], [], [], []

    for code, df in indices.items():
        if df is None or df.empty:
            continue
        if not all(c in df.columns for c in ["close", "high", "low"]):
            continue

        w = index_weights.get(code, 1.0 / max(len(indices), 1))

        atr_vals.append((_compute_atr_ratio(df), w))
        amp_vals.append((_compute_amplitude(df), w))
        vola_pct_vals.append((_compute_volatility_percentile(df), w))
        vix_vals.append((_compute_vix_equivalent(df), w))

    if not atr_vals:
        return MarketVolatilityResult(score=0.0, details={"error": "insufficient data"})

    avg_atr_ratio = _safe_weighted_avg(atr_vals)
    avg_amplitude = _safe_weighted_avg(amp_vals)
    avg_vola_percentile = _safe_weighted_avg(vola_pct_vals)
    avg_vix = _safe_weighted_avg(vix_vals)

    weights = get_factor_weights("volatility")
    w_atr = weights.get("atr_ratio_weight", 0.30)
    w_amp = weights.get("amplitude_weight", 0.30)
    w_vola = weights.get("volatility_percentile_weight", 0.25)
    w_vix = weights.get("vix_equivalent_weight", 0.15)

    # 波动率高 → 风险大 → 分数低（反向处理）
    # ATR 比、振幅、VIX 都是数值越大越危险，需要反转
    s_atr = 100.0 - normalize(avg_atr_ratio, get_norm_range("volatility", "atr_ratio"))
    s_amp = 100.0 - normalize(avg_amplitude, get_norm_range("volatility", "amplitude"))
    # 波动率百分位：过高（>0.9）表示异常波动 → 分数低
    s_vola = 100.0 - normalize(
        avg_vola_percentile, get_norm_range("volatility", "vola_percentile")
    )
    # VIX等效：越高越危险 → 反转
    s_vix = 100.0 - normalize(avg_vix, [10.0, 40.0])

    score = (
        s_atr * w_atr
        + s_amp * w_amp
        + s_vola * w_vola
        + s_vix * w_vix
    )

    return MarketVolatilityResult(
        score=round(score, 2),
        avg_atr_ratio=round(avg_atr_ratio, 6),
        avg_amplitude=round(avg_amplitude, 4),
        volatility_percentile=round(avg_vola_percentile, 4),
        details={
            "avg_vix_equivalent": round(avg_vix, 2),
            "sub_scores": {
                "atr_ratio": round(s_atr, 2),
                "amplitude": round(s_amp, 2),
                "volatility_percentile": round(s_vola, 2),
                "vix_equivalent": round(s_vix, 2),
            },
        },
    )
