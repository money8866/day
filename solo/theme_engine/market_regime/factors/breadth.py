"""MarketBreadthFactor — 市场宽度评分."""

from __future__ import annotations

import logging

from ..config import get_factor_weights, get_norm_range
from ..data import MarketDataFetcher
from ..models import MarketBreadthResult

logger = logging.getLogger(__name__)


def normalize(value: float, norm_range) -> float:
    """将值归一化到 0-100 区间."""
    lo, hi = norm_range
    if hi == lo:
        return 50.0
    clipped = max(lo, min(hi, value))
    return (clipped - lo) / (hi - lo) * 100.0


async def calc_market_breadth(
    fetcher: MarketDataFetcher, trade_date: str
) -> MarketBreadthResult:
    """计算市场宽度评分."""
    data = await fetcher.get_market_breadth(trade_date)
    if not data:
        logger.warning("无市场宽度数据，返回默认评分 0")
        return MarketBreadthResult(score=0.0, details={"error": "no data"})

    up_count = data.get("up_count", 0)
    down_count = data.get("down_count", 0)
    up_ratio = data.get("up_ratio", 0.5)
    new_high = data.get("new_high_20d", 0)
    new_low = data.get("new_low_20d", 0)
    consecutive_up = data.get("consecutive_up_count", 0)
    ma20_above_ratio = data.get("ma20_above_ratio", 0.5)

    # ── 子因子计算 ──

    # 1) up_ratio 直接用传入值
    s_up_ratio = normalize(up_ratio, get_norm_range("breadth", "up_ratio"))

    # 2) 新高新低比 (new_high + 1) / (new_low + 1)
    new_high_low_ratio = (new_high + 1.0) / (new_low + 1.0)
    s_new_high_low = normalize(
        new_high_low_ratio, get_norm_range("breadth", "new_high_low_ratio")
    )

    # 3) 涨跌比: up/down, 根据 >1 或 <1 选择不同 norm
    ad_raw = up_count / max(down_count, 1)
    if ad_raw >= 1.0:
        s_ad = normalize(ad_raw, get_norm_range("breadth", "ad_ratio_positive"))
    else:
        # 取负值，使用负向 norm
        s_ad = normalize(-1.0 / max(ad_raw, 0.01), get_norm_range("breadth", "ad_ratio_negative"))

    # 4) 连续上涨天数
    s_consecutive = normalize(
        float(consecutive_up), get_norm_range("breadth", "consecutive_up")
    )

    # 5) 站上MA20比例
    s_ma20 = normalize(ma20_above_ratio, get_norm_range("breadth", "ma20_ratio"))

    weights = get_factor_weights("breadth")
    w_up = weights.get("up_ratio_weight", 0.25)
    w_hl = weights.get("new_high_low_ratio_weight", 0.25)
    w_ad = weights.get("ad_ratio_weight", 0.20)
    w_consecutive = weights.get("consecutive_up_weight", 0.15)
    w_ma20 = weights.get("ma20_above_ratio_weight", 0.15)

    score = (
        s_up_ratio * w_up
        + s_new_high_low * w_hl
        + s_ad * w_ad
        + s_consecutive * w_consecutive
        + s_ma20 * w_ma20
    )

    return MarketBreadthResult(
        score=round(score, 2),
        up_ratio=round(up_ratio, 4),
        new_high_low_ratio=round(new_high_low_ratio, 4),
        consecutive_up_count=consecutive_up,
        advance_decline_ratio=round(ad_raw, 4),
        details={
            "up_count": up_count,
            "down_count": down_count,
            "new_high_20d": new_high,
            "new_low_20d": new_low,
            "ma20_above_ratio": ma20_above_ratio,
            "sub_scores": {
                "up_ratio": round(s_up_ratio, 2),
                "new_high_low_ratio": round(s_new_high_low, 2),
                "ad_ratio": round(s_ad, 2),
                "consecutive_up": round(s_consecutive, 2),
                "ma20_above_ratio": round(s_ma20, 2),
            },
        },
    )
