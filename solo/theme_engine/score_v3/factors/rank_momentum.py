"""Rank Momentum Factor — 主题排名变化评分.

使用ETF收益率加速度 + 成分股涨跌比作为排名动量的代理指标。
排名上升越快，分数越高。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

import numpy as np

from theme_engine.score_v3.config import get_factor_weights, get_threshold
from theme_engine.score_v3.models import RankMomentumResult

logger = logging.getLogger(__name__)


async def calc_rank_momentum(
    theme_code: str,
    trade_date: str,
    etf_df=None,
    enriched_stocks: Optional[List[Dict[str, Any]]] = None,
    **kwargs,
) -> RankMomentumResult:
    """计算排名动量评分.

    使用ETF收益率加速度 + 成分股涨跌比作为代理:
    - ETF加速度: 5日收益率 - 20日收益率, 正值表示加速
    - 成分股涨跌比: 今日上涨比例, 越高说明扩散越好
    - 综合得分 = ETF加速度分 × 0.7 + 涨跌比分 × 0.3

    Returns:
        RankMomentumResult with score 0~100
    """
    await asyncio.sleep(0)

    result = RankMomentumResult()

    # 1. ETF收益率加速度
    etf_momentum_score = 0.0
    if etf_df is not None and not etf_df.empty and "close" in etf_df.columns:
        closes = etf_df["close"].values
        if len(closes) >= 20:
            ret_5d = (closes[-1] / closes[-5] - 1) * 100
            ret_20d = (closes[-1] / closes[-20] - 1) * 100
        elif len(closes) >= 5:
            ret_5d = (closes[-1] / closes[-5] - 1) * 100
            ret_20d = 0.0
        else:
            ret_5d = 0.0
            ret_20d = 0.0

        # 加速度 = 5日收益 - 20日收益 (正值表示近期加速)
        acceleration = ret_5d - ret_20d

        # 归一化: acceleration在[-10, +10]区间映射到0~100
        etf_momentum_score = max(0.0, min(100.0, (acceleration + 10) / 20 * 100))

    # 2. 成分股涨跌比动量
    stock_momentum_score = 50.0
    if enriched_stocks:
        valid = [s for s in enriched_stocks if s.get("pct_chg") is not None]
        if valid:
            up_ratio = sum(1 for s in valid if s.get("pct_chg", 0) > 0) / len(valid)
            stock_momentum_score = up_ratio * 100

    # 综合评分: ETF加速度占主导, 成分股扩散辅助
    score = etf_momentum_score * 0.7 + stock_momentum_score * 0.3
    score = max(0.0, min(100.0, score))

    result.score = score
    result.details = {
        "etf_acceleration": round(etf_momentum_score, 2),
        "stock_up_ratio": round(stock_momentum_score, 2),
        "composite": round(score, 2),
    }

    return result
