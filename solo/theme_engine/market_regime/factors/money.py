"""MarketMoneyFactor — 市场资金评分."""

from __future__ import annotations

import logging

from ..config import get_factor_weights, get_norm_range
from ..data import MarketDataFetcher
from ..models import MarketMoneyResult

logger = logging.getLogger(__name__)


def normalize(value: float, norm_range) -> float:
    """将值归一化到 0-100 区间."""
    lo, hi = norm_range
    if hi == lo:
        return 50.0
    clipped = max(lo, min(hi, value))
    return (clipped - lo) / (hi - lo) * 100.0


async def calc_market_money(
    fetcher: MarketDataFetcher, trade_date: str
) -> MarketMoneyResult:
    """计算市场资金评分."""
    data = await fetcher.get_money_flow(trade_date)
    if not data:
        logger.warning("无资金流数据，返回默认评分 0")
        return MarketMoneyResult(score=0.0, details={"error": "no data"})

    etf_net_inflow = data.get("etf_net_inflow", 0.0)
    margin_balance_change = data.get("margin_balance_change", 0.0)
    northbound_flow = data.get("northbound_flow", 0.0)
    main_net_inflow = data.get("main_net_inflow", 0.0)

    # ── 子因子计算 ──

    # 1) ETF净流入（亿元）
    s_etf = normalize(
        etf_net_inflow, get_norm_range("money", "etf_inflow")
    )

    # 2) 融资余额变化（%）
    s_margin = normalize(
        margin_balance_change, get_norm_range("money", "margin_change")
    )

    # 3) 北向资金（亿元）
    s_north = normalize(
        northbound_flow, get_norm_range("money", "northbound")
    )

    # 4) 主力净流入（亿元）
    s_main = normalize(
        main_net_inflow, get_norm_range("money", "main_inflow")
    )

    weights = get_factor_weights("money")
    w_etf = weights.get("etf_net_inflow_norm", 0.25)
    w_margin = weights.get("margin_change_norm", 0.20)
    w_north = weights.get("northbound_flow_norm", 0.25)
    w_main = weights.get("main_inflow_norm", 0.30)

    score = (
        s_etf * w_etf
        + s_margin * w_margin
        + s_north * w_north
        + s_main * w_main
    )

    return MarketMoneyResult(
        score=round(score, 2),
        etf_net_inflow=round(etf_net_inflow, 2),
        margin_balance_change=round(margin_balance_change, 4),
        northbound_flow=round(northbound_flow, 2),
        main_net_inflow=round(main_net_inflow, 2),
        details={
            "sub_scores": {
                "etf_net_inflow": round(s_etf, 2),
                "margin_change": round(s_margin, 2),
                "northbound_flow": round(s_north, 2),
                "main_inflow": round(s_main, 2),
            },
            "weights": weights,
        },
    )
