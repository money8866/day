"""MarketLiquidityFactor — 市场成交额评分."""

from __future__ import annotations

import logging

from ..config import get_factor_weights, get_norm_range
from ..data import MarketDataFetcher
from ..models import MarketLiquidityResult

logger = logging.getLogger(__name__)


def normalize(value: float, norm_range) -> float:
    """将值归一化到 0-100 区间."""
    lo, hi = norm_range
    if hi == lo:
        return 50.0
    clipped = max(lo, min(hi, value))
    return (clipped - lo) / (hi - lo) * 100.0


async def calc_market_liquidity(
    fetcher: MarketDataFetcher, trade_date: str
) -> MarketLiquidityResult:
    """计算市场成交额评分."""
    data = await fetcher.get_market_amount(trade_date)
    if not data:
        logger.warning("无成交额数据，返回默认评分 0")
        return MarketLiquidityResult(score=0.0, details={"error": "no data"})

    total_amount = data.get("total_amount", 0.0)
    amount_ma20 = data.get("amount_ma20", 0.0)
    amount_change_pct = data.get("amount_change_pct", 0.0)
    etf_amount_change_pct = data.get("etf_amount_change_pct", 0.0)
    amount_percentile = data.get("amount_percentile", 0.5)

    # ── 子因子计算 ──

    # 1) 成交额 / 20日均值比
    amount_ma20_ratio = (
        total_amount / amount_ma20 if amount_ma20 > 0 else 1.0
    )
    s_ma20_ratio = normalize(
        amount_ma20_ratio, get_norm_range("liquidity", "amount_ma20_ratio")
    )

    # 2) 5日成交额变化率
    s_change_5d = normalize(
        amount_change_pct, get_norm_range("liquidity", "amount_change_5d")
    )

    # 3) ETF成交额变化率
    s_etf = normalize(
        etf_amount_change_pct, get_norm_range("liquidity", "etf_amount_change")
    )

    # 4) 成交额近60日百分位
    s_percentile = normalize(
        amount_percentile * 100.0, [10.0, 90.0]
    )

    weights = get_factor_weights("liquidity")
    w_ma20 = weights.get("amount_ma20_ratio_weight", 0.35)
    w_change = weights.get("amount_change_5d_weight", 0.25)
    w_etf = weights.get("etf_amount_change_weight", 0.20)
    w_percentile = weights.get("amount_percentile_weight", 0.20)

    score = (
        s_ma20_ratio * w_ma20
        + s_change_5d * w_change
        + s_etf * w_etf
        + s_percentile * w_percentile
    )

    return MarketLiquidityResult(
        score=round(score, 2),
        total_amount=round(total_amount, 2),
        amount_ma20=round(amount_ma20, 2),
        amount_change_pct=round(amount_change_pct, 4),
        etf_amount_change_pct=round(etf_amount_change_pct, 4),
        details={
            "amount_ma20_ratio": round(amount_ma20_ratio, 4),
            "amount_percentile": round(amount_percentile, 4),
            "sub_scores": {
                "amount_ma20_ratio": round(s_ma20_ratio, 2),
                "amount_change_5d": round(s_change_5d, 2),
                "etf_amount_change": round(s_etf, 2),
                "amount_percentile": round(s_percentile, 2),
            },
        },
    )
