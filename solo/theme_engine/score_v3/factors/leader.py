"""Leader Factor — 龙头质量评分.

包含:
- 龙头: TOP5 综合分 (涨幅+成交额+Alpha) → 持续性追踪
- 中军: 大市值+大成交额+非涨停 → 持续性追踪
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


# 中军门槛
# 日成交额 > 2亿 → amount > 200000千元 (tushare单位)
# 中军特点: 大成交额 + 涨跌幅温和 + 非涨停
_ZHONGJUN_AMT_TH = 200_000      # 2亿 (千元)
_ZHONGJUN_PCT_MIN = -6.0        # 跌幅下限 (不暴跌)
_ZHONGJUN_PCT_MAX = 6.0         # 涨幅上限 (不暴涨)


def _calc_composite(s: dict) -> float:
    """计算个股综合分 (用于龙头排序)."""
    alpha = s.get("alpha", s.get("pct_chg", 0))
    amount = s.get("amount", 0) or 0
    pct = s.get("pct_chg", 0) or 0
    amt_score = min(100, math.log(amount + 1) / 10) if amount > 0 else 0
    return pct * 0.40 + amt_score * 0.30 + alpha * 0.30


async def calc_leader(
    theme_code: str,
    trade_date: str,
    enriched_stocks: List[Dict[str, Any]],
    prev_leaders: Optional[List[str]] = None,      # 前一日龙头名单
    prev_zhongjun: Optional[List[str]] = None,      # 前一日中军名单
    **kwargs,
) -> LeaderResult:
    """计算龙头质量评分 + 持续性追踪 + 中军识别.

    Args:
        enriched_stocks: 已富化的成分股列表
        prev_leaders: 前一日龙头名单 (用于持续性判定)
        prev_zhongjun: 前一日中军名单
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

    prev_leaders = prev_leaders or []
    prev_zhongjun = prev_zhongjun or []

    # ── 计算每只股票的综合分 (用于龙头排序) ──
    for s in valid:
        s["_composite"] = _calc_composite(s)

    sorted_stocks = sorted(valid, key=lambda x: x.get("_composite", 0), reverse=True)

    # ── 龙头 TOP5 ──
    top5 = sorted_stocks[:5]
    top5_names = [s.get("name", s.get("code", "")) for s in top5]
    result.top_leaders = top5_names

    # ── 持续性龙头判定 ──
    persistent_leaders = []
    persistent_days: Dict[str, int] = {}
    for name in top5_names:
        if name in prev_leaders:
            days = 2  # 至少2天
            persistent_leaders.append(name)
            persistent_days[name] = days
    result.persistent_leaders = persistent_leaders
    result.persistent_days = persistent_days

    # ── 中军识别 ──
    # 标准: 日成交额>2亿 + 涨跌幅温和 + 非涨停 + 主题内排名前50%
    zhongjun_candidates = []
    half_idx = max(2, len(sorted_stocks) // 2)
    for s in sorted_stocks[:half_idx]:
        name = s.get("name", s.get("code", ""))
        amt = s.get("amount", 0) or 0
        pct = s.get("pct_chg", 0) or 0
        is_limit = s.get("limit_up", False)
        if amt >= _ZHONGJUN_AMT_TH and _ZHONGJUN_PCT_MIN <= pct <= _ZHONGJUN_PCT_MAX and not is_limit:
            zhongjun_candidates.append(name)

    # 取前3 (避免过多)
    zhongjun = zhongjun_candidates[:3]
    result.zhongjun = zhongjun

    # 持续性中军
    zhongjun_days: Dict[str, int] = {}
    for name in zhongjun:
        if name in prev_zhongjun:
            zhongjun_days[name] = 2
    result.zhongjun_days = zhongjun_days

    # ── 龙头质量评分 (原有逻辑不变) ──
    if not sorted_stocks:
        return result

    # Alpha20
    avg_alpha = sum(s.get("alpha", s.get("pct_chg", 0)) for s in top5) / len(top5)
    nr = get_norm_range("leader", "alpha")
    alpha_score = normalize(avg_alpha, nr)

    # 相对强度
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

    # 机构资金
    inst_score = normalize(vol_ratio * 50, [-100, 200])

    # 趋势
    ma20_up = sum(1 for s in top5 if s.get("above_ma20", False))
    trend_score = (ma20_up / len(top5)) * 100

    # 筹码
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
    result.details = {
        "sub": {k: round(v, 2) for k, v in sub.items()},
        "top5": top5_names,
        "persistent": persistent_leaders,
        "zhongjun": zhongjun,
    }

    return result
