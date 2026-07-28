"""
预期差评分模块 V2 — Expectation Gap Score (Proxy Model)

由于缺乏分析师一致预期数据，采用代理预期模型：
1. 行业基准：同行业公司过去N个季度净利润增速中位数
2. 公司增长：当前预告净利润增速
3. 超额增长(gap) = 公司增长 - 行业基准
4. 增长加速度：比较最近季度 vs 过去季度的增速变化

整体逻辑：
  gap > 100%  → 90-100分
  gap 50-100% → 75-90分
  gap 20-50%  → 60-75分
  gap < 20%   → 40-60分

加分项：增长加速度（利润加速+营收加速）
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .config import get_config
from .models import ExpectationGapV2Result

logger = logging.getLogger(__name__)


def _map_gap_to_score(gap: float, cfg) -> tuple[float, str]:
    """将超额增长(gap)映射到0-100分。

    Args:
        gap: 超额增长百分比。
        cfg: ExpectationGapV2Config 实例。

    Returns:
        (分数, 描述)
    """
    if gap > 100.0:
        score = cfg.gap_over_100_score
        desc = f"大幅超预期: gap={gap:+.2f}%"
    elif gap > 50.0:
        # 50%-100% 线性映射到 75-90
        ratio = (gap - 50.0) / 50.0
        score = cfg.gap_50_100_score_min + ratio * (cfg.gap_50_100_score_max - cfg.gap_50_100_score_min)
        desc = f"明显超预期: gap={gap:+.2f}%"
    elif gap > 20.0:
        # 20%-50% 线性映射到 60-75
        ratio = (gap - 20.0) / 30.0
        score = cfg.gap_20_50_score_min + ratio * (cfg.gap_20_50_score_max - cfg.gap_20_50_score_min)
        desc = f"小幅超预期: gap={gap:+.2f}%"
    else:
        # <20% 线性映射到 40-60
        ratio = max(0.0, (gap + 100.0) / 120.0)  # gap 可能为负
        score = cfg.gap_under_20_score_min + ratio * (cfg.gap_under_20_score_max - cfg.gap_under_20_score_min)
        desc = f"符合/低于预期: gap={gap:+.2f}%"

    return max(0.0, min(100.0, score)), desc


def _compute_acceleration(financial_quarters: list) -> tuple[float, float, list[str]]:
    """计算增长加速度。

    比较最近季度与之前季度的扣非净利润增速变化。

    Args:
        financial_quarters: 按时间降序排列的FinancialData列表。

    Returns:
        (利润加速度, 营收加速度, 逻辑说明列表)
    """
    logic: list[str] = []
    if len(financial_quarters) < 2:
        return 0.0, 0.0, logic

    latest = financial_quarters[0]
    prior = financial_quarters[1]

    profit_accel = 0.0
    revenue_accel = 0.0

    if latest.deducted_yoy != 0.0 and prior.deducted_yoy != 0.0:
        profit_accel = latest.deducted_yoy - prior.deducted_yoy
        logic.append(f"利润加速度: {latest.deducted_yoy:+.2f}% - {prior.deducted_yoy:+.2f}% = {profit_accel:+.2f}%")

    if latest.revenue_yoy != 0.0 and prior.revenue_yoy != 0.0:
        revenue_accel = latest.revenue_yoy - prior.revenue_yoy
        logic.append(f"营收加速度: {latest.revenue_yoy:+.2f}% - {prior.revenue_yoy:+.2f}% = {revenue_accel:+.2f}%")

    return profit_accel, revenue_accel, logic


def score_expectation_gap(
    ts_code: str,
    data_source: Any,
) -> "ExpectationGapResult":
    """（兼容接口）计算预期差评分——返回旧版 ExpectationGapResult。

    内部调用 calc_expectation_gap V2 引擎，将结果映射为旧版格式。
    保持向后兼容，不删除原有 API。

    Args:
        ts_code: 股票代码。
        data_source: 数据源。

    Returns:
        ExpectationGapResult: 旧版预期差评分结果。
    """
    v2_result = calc_expectation_gap(ts_code, data_source)
    from .models import ExpectationGapResult
    return ExpectationGapResult(
        score=v2_result.score,
        surprise_type="positive" if v2_result.gap > 10 else "neutral" if v2_result.gap > -10 else "negative",
        actual_pct=v2_result.company_growth,
        expected_pct=v2_result.industry_growth,
        gap_pct=round(v2_result.gap, 2),
        logic=v2_result.logic,
    )


def calc_expectation_gap(
    ts_code: str,
    data_source: Any,
) -> ExpectationGapV2Result:
    """计算预期差V2评分。

    使用代理预期模型：
    1. 行业基准增速 = 同行业公司扣非净利润增速中位数
    2. 公司增速 = 预告净利润增速
    3. gap = 公司增速 - 行业基准增速
    4. 加速度 = 最近季度增速变化
    5. 综合评分 = gap_score + acceleration_bonus

    Args:
        ts_code: 股票代码。
        data_source: 数据源（需提供 get_forecast, get_industry_financial_benchmark,
                     get_financial_quarters 接口）。

    Returns:
        ExpectationGapV2Result: 预期差V2评分结果。
    """
    cfg = get_config().expectation_gap_v2
    all_logic: list[str] = []

    # 1. 获取公司预告增速
    forecast_data = data_source.get_forecast(ts_code) if hasattr(data_source, "get_forecast") else None
    company_growth = 0.0

    if forecast_data is not None:
        p_min = getattr(forecast_data, "p_change_min", 0.0) or 0.0
        p_max = getattr(forecast_data, "p_change_max", 0.0) or 0.0
        company_growth = (p_min + p_max) / 2.0
        all_logic.append(f"公司预告增速: {company_growth:+.2f}%")
    else:
        all_logic.append("无法获取预告数据，使用财务数据中的扣非增速")

        # 回退到财务数据
        fin = data_source.get_financial(ts_code) if hasattr(data_source, "get_financial") else None
        if fin is not None:
            company_growth = fin.deducted_yoy
            all_logic.append(f"财务扣非增速: {company_growth:+.2f}%")

    # 2. 获取行业基准增速
    industry_data: dict[str, float] = {}
    if hasattr(data_source, "get_industry_financial_benchmark"):
        industry_data = data_source.get_industry_financial_benchmark(ts_code)

    industry_growth = 0.0
    peer_count = 0
    if industry_data:
        if cfg.benchmark_stat == "median":
            industry_growth = industry_data.get("industry_median_growth", 0.0)
        else:
            industry_growth = industry_data.get("industry_mean_growth", 0.0)
        peer_count = industry_data.get("peer_count", 0)
        all_logic.append(f"行业{cfg.benchmark_stat}增速: {industry_growth:+.2f}%")
        all_logic.append(f"可比公司数: {peer_count}")
    else:
        all_logic.append("无法获取行业基准数据，使用中性基准(0%)")

    # 3. 计算超额增长(gap)
    gap = company_growth - industry_growth
    all_logic.append(f"超额增长(gap): {company_growth:+.2f}% - {industry_growth:+.2f}% = {gap:+.2f}%")

    # 4. 增长加速度
    profit_accel = 0.0
    revenue_accel = 0.0
    if hasattr(data_source, "get_financial_quarters"):
        fin_quarters = data_source.get_financial_quarters(ts_code, cfg.industry_lookback_quarters)
        profit_accel, revenue_accel, accel_logic = _compute_acceleration(fin_quarters)
        all_logic.extend(accel_logic)

    # 综合加速度
    acceleration = profit_accel * 0.6 + revenue_accel * 0.4

    # 5. gap -> 分数
    base_score, desc = _map_gap_to_score(gap, cfg)
    all_logic.append(f"基础分({desc}): {base_score:.1f}分")

    # 6. 加速度加分
    accel_bonus = 0.0
    if acceleration > 0:
        # 加速度为正数时加分，线性映射到 max_acceleration_bonus
        accel_bonus = min(cfg.max_acceleration_bonus, acceleration / 20.0 * cfg.max_acceleration_bonus)
        all_logic.append(f"加速度加分({acceleration:+.2f}%): +{accel_bonus:.1f}分")
    elif acceleration < 0:
        all_logic.append(f"增长减速({acceleration:+.2f}%)，无加分")

    final_score = base_score + accel_bonus * cfg.acceleration_bonus_weight
    final_score = max(0.0, min(100.0, final_score))
    all_logic.append(f"最终预期差评分: {final_score:.1f}分")

    return ExpectationGapV2Result(
        score=round(final_score, 2),
        industry_growth=round(industry_growth, 2),
        company_growth=round(company_growth, 2),
        gap=round(gap, 2),
        acceleration=round(acceleration, 2),
        profit_acceleration=round(profit_accel, 2),
        revenue_acceleration=round(revenue_accel, 2),
        industry_median=round(industry_data.get("industry_median_growth", 0.0), 2) if industry_data else 0.0,
        industry_mean=round(industry_data.get("industry_mean_growth", 0.0), 2) if industry_data else 0.0,
        peer_count=peer_count,
        logic=all_logic,
    )
