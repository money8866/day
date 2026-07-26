"""Leader Factor — 龙头质量评分.

Alpha20、相对强度、成交额、机构资金、趋势、筹码、历史新高。
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Dict, List, Optional

from theme_engine.score_v3.config import get_factor_weights, get_norm_range
from theme_engine.score_v3.models import LeaderResult

logger = logging.getLogger(__name__)


def normalize(value: float, norm_range) -> float:
    lo, hi = norm_range
    if hi == lo:
        return 50.0
    clipped = max(lo, min(hi, value))
    return (clipped - lo) / (hi - lo) * 100.0


async def calc_leader(
    theme_code: str,
    trade_date: str,
    enriched_stocks: List[Dict[str, Any]],
    **kwargs,
) -> LeaderResult:
    """计算龙头质量评分.

    Args:
        enriched_stocks: 已富化的成分股列表
    """
    await asyncio.sleep(0)

    result = LeaderResult()
    if not enriched_stocks:
        return result

    weights = get_factor_weights("leader")
    if not weights:
        return result

    valid = [s for s in enriched_stocks if s.get("pct_chg") is not None]
    if not valid:
        return result

    # 按综合得分识别龙头 (涨幅 + 成交额 + Alpha)
    for s in valid:
        alpha = s.get("alpha", s.get("pct_chg", 0))
        amount = s.get("amount", 0) or 0
        pct = s.get("pct_chg", 0) or 0
        # 综合分: 涨幅权重0.4, 成交额log归一化0.3, Alpha0.3
        amt_score = min(100, math.log(amount + 1) / 10) if amount > 0 else 0
        s["_composite"] = pct * 0.4 + amt_score * 0.3 + alpha * 0.3

    sorted_stocks = sorted(valid, key=lambda x: x.get("_composite", 0), reverse=True)

    # TOP5 作为龙头候选 (显示股票名称)
    top_leaders = [s.get("name", s.get("code", "")) for s in sorted_stocks[:5]]
    result.top_leaders = top_leaders

    if not sorted_stocks:
        return result

    top5 = sorted_stocks[:5]

    # Alpha20
    avg_alpha = sum(s.get("alpha", s.get("pct_chg", 0)) for s in top5) / len(top5)
    nr = get_norm_range("leader", "alpha")
    alpha_score = normalize(avg_alpha, nr)

    # 相对强度 (相对主题内其他股票的超额收益)
    avg_all = sum(s.get("pct_chg", 0) for s in valid) / len(valid)
    avg_top = sum(s.get("pct_chg", 0) for s in top5) / len(top5)
    rs = avg_top - avg_all
    nr2 = get_norm_range("leader", "rs")
    rs_score = normalize(rs, nr2)

    # 成交额
    avg_amt = sum(s.get("amount", 0) or 0 for s in top5) / len(top5)
    avg_amt_all = sum(s.get("amount", 0) or 0 for s in valid) / len(valid)
    vol_ratio = avg_amt / avg_amt_all if avg_amt_all > 0 else 1.0
    nr3 = get_norm_range("leader", "volume_ratio")
    vol_score = normalize(vol_ratio, nr3)

    # 机构资金 (简化: 用成交额占比近似)
    inst_score = normalize(vol_ratio * 50, [-100, 200])

    # 趋势 (龙头相对MA20)
    ma20_up = sum(1 for s in top5 if s.get("above_ma20", False))
    trend_score = (ma20_up / len(top5)) * 100

    # 筹码 (简化)
    chip_score = 50.0

    # 历史新高
    new_high = sum(1 for s in top5 if s.get("new_high_20d", False))
    new_high_score = (new_high / len(top5)) * 100

    sub = {
        "alpha_20": alpha_score,
        "relative_strength": rs_score,
        "volume": vol_score,
        "institutional_money": inst_score,
        "trend": trend_score,
        "chip_score": chip_score,
        "new_high": new_high_score,
    }

    total_w = sum(weights.values())
    score = 0.0
    for key, w in weights.items():
        for sk, sv in sub.items():
            if sk in key:
                score += sv * w
                break

    result.score = score / total_w if total_w > 0 else 0.0
    result.alpha_20 = round(avg_alpha, 2)
    result.relative_strength = round(rs, 2)
    result.volume_score = round(vol_score, 2)
    result.institutional_money = round(inst_score, 2)
    result.trend_score = round(trend_score, 2)
    result.chip_score = round(chip_score, 2)
    result.new_high_score = round(new_high_score, 2)
    result.details = {"sub": {k: round(v, 2) for k, v in sub.items()}, "top5": top_leaders}

    return result
