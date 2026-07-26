"""Leader Expansion Factor — 龙头扩散评分.

使用当天截面数据计算:
- 龙头宽度: 涨幅>5%的个股占比 (反映龙头强度)
- 中军宽度: 涨幅2%~5%的个股占比 (反映扩散程度)
- 综合活跃度: 上涨个股占比 + 涨停占比
- 集中度: 前3龙头涨幅占比 (过高=不扩散, 适中=健康扩散)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from theme_engine.score_v3.config import get_factor_weights
from theme_engine.score_v3.models import LeaderExpandResult

logger = logging.getLogger(__name__)


async def calc_leader_expand(
    theme_code: str,
    trade_date: str,
    enriched_stocks: List[Dict[str, Any]],
    **kwargs,
) -> LeaderExpandResult:
    """计算龙头扩散评分.

    从当天成分股数据计算:
    - leader_width: 涨幅>5%的占比 → 龙头强度
    - mid_width: 涨幅2~5%的占比 → 扩散程度
    - activity: 上涨占比 → 整体活跃度
    - concentration: 前3涨幅占比 → 集中度 (过高=抱团, 过低=无龙头)
    """
    await asyncio.sleep(0)

    result = LeaderExpandResult()
    if not enriched_stocks:
        return result

    weights = get_factor_weights("leader_expand")
    if not weights:
        return result

    valid = [s for s in enriched_stocks if s.get("pct_chg") is not None]
    if not valid:
        return result

    n = len(valid)
    pct_chgs = sorted([s.get("pct_chg", 0) for s in valid], reverse=True)
    total_gain = sum(max(0, c) for c in pct_chgs)

    # 1. 龙头宽度: 涨幅>5%的占比
    leader_count = sum(1 for c in pct_chgs if c > 5)
    leader_width = leader_count / n * 100  # 0~100

    # 2. 中军宽度: 涨幅2~5%的占比
    mid_count = sum(1 for c in pct_chgs if 2 < c <= 5)
    mid_width = mid_count / n * 100  # 0~100

    # 3. 整体活跃度: 上涨占比
    up_count = sum(1 for c in pct_chgs if c > 0)
    activity = up_count / n * 100  # 0~100

    # 4. 集中度: 前3涨幅占总涨幅的比例 (适中最好)
    concentration = 0.0
    if total_gain > 0 and n >= 3:
        top3_gain = sum(max(0, c) for c in pct_chgs[:3])
        concentration = top3_gain / total_gain * 100  # 0~100

    # 集中度适中得分: 30~60%集中度最优
    if concentration < 20:
        conc_score = concentration / 20 * 70  # 太分散
    elif concentration <= 60:
        conc_score = 100  # 适中 = 最佳
    elif concentration <= 80:
        conc_score = 100 - (concentration - 60) / 20 * 30  # 略集中
    else:
        conc_score = max(0, 70 - (concentration - 80) / 20 * 70)  # 太集中

    sub = {
        "leader_width": leader_width,
        "mid_width": mid_width,
        "activity": activity,
        "concentration": conc_score,
    }

    total_w = sum(weights.values())
    score = 0.0
    for key, w in weights.items():
        if "leader_count" in key:
            score += leader_width * w
        elif "mid_cap" in key:
            score += mid_width * w
        elif "strong_stock" in key:
            score += activity * w
        elif "leader_quality" in key:
            score += conc_score * w

    result.score = score / total_w if total_w > 0 else 0.0
    result.leader_count_change = leader_count
    result.mid_cap_count_change = mid_count
    result.strong_stock_count_change = up_count
    result.details = {"sub": {k: round(v, 2) for k, v in sub.items()}}

    return result
