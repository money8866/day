"""
ELD V2 事件质量评分

检查业绩预告中的非经常性损益，评估事件质量。
"""

from __future__ import annotations

import re
from typing import Final, Optional

from .config import EventFilterConfig
from .constants import STAR_THRESHOLDS, StarRating
from .models import EventQualityResult, FinancialData, ForecastData


# ──────────────────────────────────────────────
# 关键字检测
# ──────────────────────────────────────────────
_NON_RECURRING_PATTERNS: Final[list[re.Pattern]] = [
    re.compile(kw, re.IGNORECASE)
    for kw in [
        "卖资产", "政府补贴", "补贴", "公允价值", "金融资产",
        "资产重组", "重组收益", "债务重组", "一次性收益",
        "非经常性", "拆迁补偿", "股权转让", "投资收益",
        "低基数", "扭亏为盈", "出售", "处置",
    ]
]


def _check_non_recurring(
    forecast: ForecastData,
    financial: FinancialData,
    config: EventFilterConfig,
) -> list[str]:
    """检查预告和财务数据中是否包含非经常性损益关键词。

    使用预告原文 summary 进行关键词匹配，比单纯匹配 type 更准确。

    Returns:
        匹配到的非经常性项目列表。
    """
    items: list[str] = []

    # 用关键字匹配预告原文摘要（summary 包含实际公告内容，如"主要系非经常性损益影响"）
    if forecast.summary:
        for kw in config.exclude_keywords:
            if kw.lower() in forecast.summary.lower():
                items.append(f"预告原文含非经常性关键词「{kw}」")
    else:
        # 无 summary 时，降级匹配 type 字段（如"扭亏"可能隐含非经常性）
        for kw in config.exclude_keywords:
            if kw.lower() in forecast.type.lower():
                items.append(f"预告类型含非经常性关键词: {kw}")
                break

    # 检测财务特征
    if financial.net_profit > 0 and financial.deducted_profit <= 0:
        items.append("净利润为正但扣非净利润为负或零，利润依赖非经常性项目")

    if financial.deducted_profit > 0 and financial.deducted_yoy < -30:
        items.append("扣非净利润正但同比大幅下滑 (>30%)，含一次性因素")

    # 去重
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)

    return unique


def _star_from_score(score: float) -> StarRating:
    """根据分数返回对应星级"""
    for threshold, star in STAR_THRESHOLDS:
        if score >= threshold:
            return star
    return StarRating.ZERO


def analyze_event_quality(
    forecast: ForecastData,
    financial: FinancialData,
    config: Optional[EventFilterConfig] = None,
) -> EventQualityResult:
    """分析事件质量，计算评分和星级。

    评分逻辑: 基准100分，依次扣减：
      1. 存在非经常性收益 → 扣20分
      2. 扣非净利润占比过低 (<70%) → 扣15分
      3. 营收负增长 → 扣10分

    Args:
        forecast: 业绩预告数据
        financial: 同期财务数据
        config: 事件过滤配置，默认使用全局配置

    Returns:
        事件质量评分结果
    """
    # ── 初始化 ──────────────────────────────
    if config is None:
        from .config import get_config

        config = get_config().event_filter

    logic: list[str] = []
    score: float = 100.0
    has_non_recurring: bool = False
    non_recurring_items: list[str] = []

    # ── 0. 预告利润增速过滤（新增） ────────────
    forecast_growth = (forecast.p_change_min + forecast.p_change_max) / 2
    if forecast_growth < config.min_forecast_growth:
        shortage = config.min_forecast_growth - forecast_growth
        penalty = min(40.0, shortage * 0.5)  # 每低1%扣0.5分，最高扣40分
        score -= penalty
        logic.append(
            f"预告利润增速 {forecast_growth:.1f}% < {config.min_forecast_growth:.0f}%: "
            f"扣{penalty:.0f}分"
        )
    else:
        logic.append(f"预告利润增速 {forecast_growth:.1f}% ≥ {config.min_forecast_growth:.0f}%: 通过")

    # ── 1. 检测非经常性项目 ──────────────────
    non_recurring_items = _check_non_recurring(forecast, financial, config)
    has_non_recurring = len(non_recurring_items) > 0

    if has_non_recurring:
        score -= 20.0
        logic.append(f"检测到非经常性项目 ({len(non_recurring_items)}项): 扣20分")
        for item in non_recurring_items[:3]:  # 最多展示3条
            logic.append(f"  - {item}")
    else:
        logic.append("无非经常性项目: 通过")

    # ── 2. 扣非净利润占比 ────────────────────
    net_profit = financial.net_profit
    deducted_profit = financial.deducted_profit
    deducted_ratio: float = 0.0

    if net_profit != 0 and deducted_profit is not None:
        deducted_ratio = deducted_profit / net_profit
    elif net_profit == 0 and deducted_profit is not None and deducted_profit > 0:
        deducted_ratio = 999.0  # 净利润为0但扣非为正，视为高占比
    else:
        deducted_ratio = 0.0

    if deducted_ratio < config.min_deducted_ratio:
        score -= 15.0
        logic.append(
            f"扣非占比 {deducted_ratio:.1%} < {config.min_deducted_ratio:.0%}: "
            f"扣15分"
        )
    else:
        logic.append(f"扣非占比 {deducted_ratio:.1%} ≥ {config.min_deducted_ratio:.0%}: 通过")

    # ── 3. 营收增长 ─────────────────────────
    revenue_growth = financial.revenue_yoy

    if revenue_growth < config.min_revenue_growth:
        score -= 10.0
        logic.append(
            f"营收增长 {revenue_growth:.1f}% < {config.min_revenue_growth:.0f}%: 扣10分"
        )
    elif revenue_growth < 0:
        score -= 5.0
        logic.append(f"营收负增长 ({revenue_growth:.1f}%): 扣5分")
    else:
        logic.append(f"营收增长 {revenue_growth:.1f}%: 通过")

    # ── 4. 分数截断 ─────────────────────────
    score = max(0.0, min(100.0, score))

    # ── 5. 星级评定 ─────────────────────────
    stars = _star_from_score(score)

    return EventQualityResult(
        score=round(score, 2),
        stars=stars,
        deducted_ratio=round(deducted_ratio, 4),
        revenue_growth=revenue_growth,
        has_non_recurring=has_non_recurring,
        non_recurring_items=non_recurring_items,
        logic=logic,
    )
