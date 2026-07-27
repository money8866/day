"""
公告新鲜度评分模块 — Announcement Freshness Score

根据公告日期距今的天数，按照衰减时间表输出新鲜度评分。
"""

from __future__ import annotations

import logging
from datetime import datetime, date
from typing import Optional

from .constants import FRESHNESS_SCHEDULE
from .models import FreshnessScoreResult

logger = logging.getLogger(__name__)

_DATE_FORMATS = ["%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"]


def _parse_date(announce_date: str) -> Optional[date]:
    """尝试多种格式解析日期"""
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(announce_date.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def _compute_days_since(announce_date: str) -> tuple[Optional[int], list[str]]:
    """计算公告距今的天数"""
    logic: list[str] = []

    parsed = _parse_date(announce_date)
    if parsed is None:
        logic.append(f"无法解析公告日期: {announce_date}")
        return None, logic

    today = date.today()
    days = (today - parsed).days

    logic.append(f"公告日期: {parsed.isoformat()}, 距今: {days}天")
    return days, logic


def score_freshness(announce_date: str) -> FreshnessScoreResult:
    """对公告新鲜度进行评分

    根据 FRESHNESS_SCHEDULE 定义的衰减时间表，
    天数越短分数越高（新鲜度越高）。

    Args:
        announce_date: 公告日期字符串（支持 %Y%m%d / %Y-%m-%d / %Y/%m/%d）

    Returns:
        FreshnessScoreResult: 新鲜度评分结果
    """
    days, logic = _compute_days_since(announce_date)

    if days is None:
        return FreshnessScoreResult(
            score=0.0,
            days_since_announce=999,
            logic=logic,
        )

    # 按 FRESHNESS_SCHEDULE 查找对应的分数
    score = 0.0
    for max_days, s in FRESHNESS_SCHEDULE:
        if days <= max_days:
            score = s
            break

    if score > 0:
        logic.append(f"新鲜度评分: {score}分（{days}天内）")
    else:
        # 超出最大天数区间，线性衰减到0
        score = max(0.0, 10.0 - (days - 60) * 0.2)
        logic.append(f"超出新鲜度窗口（{days}天），衰减评分: {score:.1f}分")

    return FreshnessScoreResult(
        score=score,
        days_since_announce=days,
        logic=logic,
    )
