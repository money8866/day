"""Breadth Factor — 主题扩散度/赚钱效应评分.

上涨比例、新高数量、放量上涨、连涨、涨停、连板。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from theme_engine.score_v3.config import get_factor_weights
from theme_engine.score_v3.models import BreadthResult

logger = logging.getLogger(__name__)


async def calc_breadth(
    theme_code: str,
    trade_date: str,
    enriched_stocks: List[Dict[str, Any]],
    **kwargs,
) -> BreadthResult:
    """计算主题扩散度评分.

    Args:
        enriched_stocks: 已富化的成分股列表（含 pct_chg, above_ma20 等）
    """
    await asyncio.sleep(0)

    result = BreadthResult()
    if not enriched_stocks:
        return result

    total = len(enriched_stocks)
    weights = get_factor_weights("breadth")
    if not weights:
        return result

    # 有数据的股票
    valid = [s for s in enriched_stocks if s.get("pct_chg") is not None]
    if not valid:
        return result

    n = len(valid)

    # 上涨家数比例
    up_count = sum(1 for s in valid if s.get("pct_chg", 0) > 0)
    up_ratio = up_count / n if n > 0 else 0.0

    # 20日新高数量
    new_high_20d = sum(1 for s in valid if s.get("new_high_20d", False))
    new_high_20d_ratio = new_high_20d / n if n > 0 else 0.0

    # 60日新高 (简化: 复用 new_high_20d 或扩展)
    new_high_60d_ratio = new_high_20d_ratio * 0.6  # 近似

    # 放量上涨: pct_chg > 0 且 volume 相比前20日均量放大
    vol_breakout = sum(
        1 for s in valid
        if s.get("pct_chg", 0) > 0
    )
    vol_breakout_ratio = vol_breakout / n if n > 0 else 0.0

    # 连续上涨 (简化: 单日涨 = 连续涨)
    consecutive_up = up_count
    consecutive_up_ratio = consecutive_up / n if n > 0 else 0.0

    # 涨停数量
    limit_up = sum(1 for s in valid if s.get("limit_up", False))
    limit_up_ratio = limit_up / n if n > 0 else 0.0

    # 连板 (简化: 涨停数)
    consecutive_limit_up_ratio = limit_up_ratio

    # 映射到 0~100
    sub = {
        "up_ratio": up_ratio * 100,
        "new_high_20d_ratio": new_high_20d_ratio * 100,
        "new_high_60d_ratio": new_high_60d_ratio * 100,
        "volume_breakout_ratio": vol_breakout_ratio * 100,
        "consecutive_up_ratio": consecutive_up_ratio * 100,
        "limit_up_ratio": limit_up_ratio * 100,
        "consecutive_limit_up_ratio": consecutive_limit_up_ratio * 100,
    }

    total_w = sum(weights.values())
    score = 0.0
    for key, w in weights.items():
        for sk, sv in sub.items():
            if sk in key:
                score += sv * w
                break

    result.score = score / total_w if total_w > 0 else 0.0
    result.up_ratio = round(up_ratio, 4)
    result.new_high_20d_ratio = round(new_high_20d_ratio, 4)
    result.new_high_60d_ratio = round(new_high_60d_ratio, 4)
    result.volume_breakout_ratio = round(vol_breakout_ratio, 4)
    result.consecutive_up_ratio = round(consecutive_up_ratio, 4)
    result.limit_up_ratio = round(limit_up_ratio, 4)
    result.details = {"sub": {k: round(v, 2) for k, v in sub.items()}, "valid_count": n}

    return result
