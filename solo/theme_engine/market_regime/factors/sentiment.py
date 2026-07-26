"""MarketSentimentFactor — 市场情绪评分."""

from __future__ import annotations

import logging

from ..config import get_factor_weights, get_norm_range
from ..data import MarketDataFetcher
from ..models import MarketSentimentResult

logger = logging.getLogger(__name__)

# 全市场股票数量估算（沪深两市）
_TOTAL_STOCKS = 5000


def normalize(value: float, norm_range) -> float:
    """将值归一化到 0-100 区间."""
    lo, hi = norm_range
    if hi == lo:
        return 50.0
    clipped = max(lo, min(hi, value))
    return (clipped - lo) / (hi - lo) * 100.0


async def calc_market_sentiment(
    fetcher: MarketDataFetcher, trade_date: str
) -> MarketSentimentResult:
    """计算市场情绪评分."""
    data = await fetcher.get_market_sentiment(trade_date)
    if not data:
        logger.warning("无市场情绪数据，返回默认评分 0")
        return MarketSentimentResult(score=0.0, details={"error": "no data"})

    limit_up = data.get("limit_up_count", 0)
    limit_down = data.get("limit_down_count", 0)
    break_rate = data.get("break_rate", 0.3)
    consecutive_height = data.get("consecutive_limit_height", 0)
    yest_limit_up_perf = data.get("yest_limit_up_perf", 0.0)
    yest_consecutive_perf = data.get("yest_consecutive_perf", 0.0)

    # ── 子因子计算 ──

    # 1) 涨停占比（反向：越高越好）
    limit_up_ratio = limit_up / _TOTAL_STOCKS
    s_limit_up = normalize(limit_up_ratio, get_norm_range("sentiment", "limit_up_ratio"))

    # 2) 跌停占比（反向：越低越好 → 分数越高）
    limit_down_ratio = limit_down / _TOTAL_STOCKS
    s_limit_down = 100.0 - normalize(
        limit_down_ratio, get_norm_range("sentiment", "limit_down_ratio")
    )

    # 3) 炸板率（反向：越低越好 → 分数越高）
    s_break = 100.0 - normalize(break_rate, get_norm_range("sentiment", "break_rate"))

    # 4) 最高连板高度
    s_height = normalize(
        float(consecutive_height), get_norm_range("sentiment", "consecutive_height")
    )

    # 5) 昨日涨停表现
    s_yest_up = normalize(
        yest_limit_up_perf, get_norm_range("sentiment", "yest_perf")
    )

    # 6) 昨日连板表现
    s_yest_consecutive = normalize(
        yest_consecutive_perf, get_norm_range("sentiment", "yest_perf")
    )

    weights = get_factor_weights("sentiment")
    w_up = weights.get("limit_up_ratio_weight", 0.20)
    w_down = weights.get("limit_down_ratio_weight", 0.20)
    w_break = weights.get("break_rate_weight", 0.15)
    w_height = weights.get("consecutive_height_weight", 0.15)
    w_yest_up = weights.get("yest_limit_up_perf_weight", 0.15)
    w_yest_consecutive = weights.get("yest_consecutive_perf_weight", 0.15)

    score = (
        s_limit_up * w_up
        + s_limit_down * w_down
        + s_break * w_break
        + s_height * w_height
        + s_yest_up * w_yest_up
        + s_yest_consecutive * w_yest_consecutive
    )

    return MarketSentimentResult(
        score=round(score, 2),
        limit_up_count=limit_up,
        limit_down_count=limit_down,
        break_rate=round(break_rate, 4),
        consecutive_limit_height=consecutive_height,
        yest_limit_up_perf=round(yest_limit_up_perf, 4),
        yest_consecutive_perf=round(yest_consecutive_perf, 4),
        details={
            "limit_up_ratio": round(limit_up_ratio, 4),
            "limit_down_ratio": round(limit_down_ratio, 4),
            "sub_scores": {
                "limit_up_ratio": round(s_limit_up, 2),
                "limit_down_ratio": round(s_limit_down, 2),
                "break_rate": round(s_break, 2),
                "consecutive_height": round(s_height, 2),
                "yest_limit_up_perf": round(s_yest_up, 2),
                "yest_consecutive_perf": round(s_yest_consecutive, 2),
            },
        },
    )
