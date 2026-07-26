"""MarketTrendFactor — 大盘趋势评分."""

from __future__ import annotations

import logging
from typing import Any, Dict

import numpy as np
import pandas as pd

from ..config import get_factor_weights, get_norm_range
from ..data import MarketDataFetcher
from ..models import MarketTrendResult

logger = logging.getLogger(__name__)

# ── 辅助函数 ──────────────────────────────────────────


def normalize(value: float, norm_range) -> float:
    """将值归一化到 0-100 区间."""
    lo, hi = norm_range
    if hi == lo:
        return 50.0
    clipped = max(lo, min(hi, value))
    return (clipped - lo) / (hi - lo) * 100.0


def _ema_position(close: pd.Series, span: int) -> float:
    """计算价格相对EMA的位置百分比."""
    if len(close) < span + 5:
        return 0.0
    ema = close.ewm(span=span, adjust=False).mean()
    pos = ((close.iloc[-1] - ema.iloc[-1]) / ema.iloc[-1]) * 100.0
    return float(pos)


def _slope_20d(close: pd.Series) -> float:
    """计算20日线性回归斜率."""
    if len(close) < 20:
        return 0.0
    y = close.iloc[-20:].values.astype(float)
    x = np.arange(len(y))
    cov = np.cov(x, y, ddof=0)
    slope = cov[0, 1] / cov[0, 0] if cov[0, 0] != 0 else 0.0
    return slope / y.mean() if y.mean() != 0 else 0.0


def _new_high_proximity(close: pd.Series, window: int = 20) -> float:
    """价格在 window 日高低点区间中的位置 (0~1)."""
    if len(close) < window:
        return 0.0
    recent = close.iloc[-window:]
    hi, lo = recent.max(), recent.min()
    if hi == lo:
        return 0.5
    return (close.iloc[-1] - lo) / (hi - lo)


def _max_drawdown(close: pd.Series) -> float:
    """最近20日最大回撤百分比."""
    if len(close) < 20:
        return 0.0
    recent = close.iloc[-20:]
    peak = recent.expanding().max()
    dd = (recent - peak) / peak
    return float(abs(dd.min()) * 100.0)


def _rsi(close: pd.Series, period: int = 14) -> float:
    """计算14日RSI."""
    if len(close) < period + 1:
        return 50.0
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rs = rs.fillna(50.0)
    return float(100.0 - 100.0 / (1.0 + rs.iloc[-1]))


def _load_index_weights() -> Dict[str, float]:
    """从配置加载指数权重."""
    from ..config import load_config
    cfg = load_config()
    return {k: float(v) for k, v in cfg.get("index_weights", {}).items()}


async def calc_market_trend(
    fetcher: MarketDataFetcher, trade_date: str
) -> MarketTrendResult:
    """计算大盘趋势评分."""
    indices = await fetcher.get_all_indices(trade_date)
    if not indices:
        logger.warning("无指数数据，返回默认趋势评分 0")
        return MarketTrendResult(score=0.0, details={"error": "no index data"})

    index_weights = _load_index_weights()
    n = len(indices)

    ema20_vals, ema60_vals, slope_vals = [], [], []
    new_high_vals, dd_vals, rsi_vals = [], [], []

    for code, df in indices.items():
        if df is None or df.empty or "close" not in df.columns:
            continue
        close = df["close"].dropna()
        if len(close) < 30:
            continue
        w = index_weights.get(code, 1.0 / n)
        ema20_vals.append((_ema_position(close, 20), w))
        ema60_vals.append((_ema_position(close, 60), w))
        slope_vals.append((_slope_20d(close), w))
        new_high_vals.append((_new_high_proximity(close, 20), w))
        dd_vals.append((_max_drawdown(close), w))
        rsi_vals.append((_rsi(close, 14), w))

    if not ema20_vals:
        return MarketTrendResult(score=0.0, details={"error": "insufficient data"})

    def weighted_avg(vals):
        total_w = sum(w for _, w in vals)
        return sum(v * w for v, w in vals) / total_w if total_w else 0.0

    avg_ema20_pos = weighted_avg(ema20_vals)
    avg_ema60_pos = weighted_avg(ema60_vals)
    avg_slope = weighted_avg(slope_vals)
    avg_new_high = weighted_avg(new_high_vals)
    avg_dd = weighted_avg(dd_vals)
    avg_rsi = weighted_avg(rsi_vals)

    weights = get_factor_weights("trend")
    w_ema20 = weights.get("ema20_pos_weight", 0.20)
    w_ema60 = weights.get("ema60_pos_weight", 0.18)
    w_slope = weights.get("slope_20d_weight", 0.20)
    w_new_high = weights.get("new_high_ratio_weight", 0.17)
    w_dd = weights.get("drawdown_weight", 0.15)
    w_rsi = weights.get("rsi_weight", 0.10)

    s_ema20 = normalize(avg_ema20_pos, get_norm_range("trend", "ema20_pos"))
    s_ema60 = normalize(avg_ema60_pos, get_norm_range("trend", "ema60_pos"))
    s_slope = normalize(avg_slope, get_norm_range("trend", "slope"))
    s_new_high = normalize(avg_new_high, get_norm_range("trend", "new_high_ratio"))
    s_dd = normalize(avg_dd, get_norm_range("trend", "drawdown"))
    s_rsi = normalize(avg_rsi, get_norm_range("trend", "rsi"))

    score = (
        s_ema20 * w_ema20
        + s_ema60 * w_ema60
        + s_slope * w_slope
        + s_new_high * w_new_high
        + s_dd * w_dd
        + s_rsi * w_rsi
    )

    return MarketTrendResult(
        score=round(score, 2),
        index_count=len(indices),
        avg_ema20_pos=round(avg_ema20_pos, 4),
        avg_ema60_pos=round(avg_ema60_pos, 4),
        avg_slope_20d=round(avg_slope, 6),
        avg_new_high_20d=round(avg_new_high, 4),
        avg_drawdown_20d=round(avg_dd, 4),
        details={
            "sub_scores": {
                "ema20_pos": round(s_ema20, 2),
                "ema60_pos": round(s_ema60, 2),
                "slope_20d": round(s_slope, 2),
                "new_high_ratio": round(s_new_high, 2),
                "drawdown": round(s_dd, 2),
                "rsi": round(s_rsi, 2),
            },
            "weights": weights,
        },
    )
