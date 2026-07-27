"""
预期差评分模块 — Expectation Gap Score

比较实际业绩预告增速与市场一致预期，
判断是否超预期、符合预期或低于预期。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .config import get_config
from .models import ExpectationGapResult

logger = logging.getLogger(__name__)


def _get_actual_forecast(ts_code: str, data_source: Any) -> Optional[float]:
    """从数据源获取实际预告净利润变动幅度（%）"""
    try:
        forecast_data = getattr(data_source, "get_forecast", None)
        if forecast_data is not None:
            result = forecast_data(ts_code)
            if result is not None:
                # 取预告上下限的中值
                p_change_min = getattr(result, "p_change_min", None) or 0.0
                p_change_max = getattr(result, "p_change_max", None) or 0.0
                if p_change_min != 0.0 or p_change_max != 0.0:
                    return (p_change_min + p_change_max) / 2.0
        return None
    except Exception as exc:
        logger.debug("获取实际预告数据失败 %s: %s", ts_code, exc)
        return None


def _get_market_consensus(ts_code: str, data_source: Any) -> Optional[float]:
    """获取市场一致预期净利润增速（%）"""
    try:
        consensus = getattr(data_source, "get_consensus", None)
        if consensus is not None:
            result = consensus(ts_code)
            if result is not None:
                return float(result)
        return None
    except Exception as exc:
        logger.debug("获取市场一致预期失败 %s: %s", ts_code, exc)
        return None


def _estimate_expected_growth(ts_code: str, data_source: Any) -> Optional[float]:
    """当无一致预期数据时，估算预期增速

    估算策略：
    1. 取行业平均增速
    2. 若不可得，取个股历史3年平均增速
    """
    try:
        # 策略1：行业平均增速
        industry_avg = getattr(data_source, "get_industry_avg_growth", None)
        if industry_avg is not None:
            result = industry_avg(ts_code)
            if result is not None:
                return float(result)

        # 策略2：个股历史3年平均增速
        historical_growth = getattr(data_source, "get_historical_avg_growth", None)
        if historical_growth is not None:
            result = historical_growth(ts_code, years=3)
            if result is not None:
                return float(result)

        return None
    except Exception as exc:
        logger.debug("估算预期增速失败 %s: %s", ts_code, exc)
        return None


def _calculate_gap(
    actual_pct: float,
    expected_pct: float,
) -> tuple[float, str, list[str]]:
    """计算预期差并分类"""
    logic: list[str] = []

    # 避免除零
    abs_expected = abs(expected_pct)
    if abs_expected < 0.01:
        gap_pct = 0.0
        logic.append(f"预期增速接近零（{expected_pct:.2f}%），预期差视为0")
        return gap_pct, "neutral", logic

    gap_pct = (actual_pct - expected_pct) / abs_expected * 100.0

    cfg = get_config().expectation_gap
    if gap_pct > cfg.surprise_threshold_pct:
        surprise_type = "positive"
        logic.append(
            f"正向惊喜: 实际{actual_pct:+.2f}% vs 预期{expected_pct:+.2f}%, "
            f"预期差{gap_pct:+.2f}%"
        )
    elif gap_pct < -cfg.surprise_threshold_pct:
        surprise_type = "negative"
        logic.append(
            f"负向失望: 实际{actual_pct:+.2f}% vs 预期{expected_pct:+.2f}%, "
            f"预期差{gap_pct:+.2f}%"
        )
    else:
        surprise_type = "neutral"
        logic.append(
            f"符合预期: 实际{actual_pct:+.2f}% vs 预期{expected_pct:+.2f}%, "
            f"预期差{gap_pct:+.2f}%"
        )

    return gap_pct, surprise_type, logic


def score_expectation_gap(
    ts_code: str,
    data_source: Any,
) -> ExpectationGapResult:
    """对预期差进行评分

    比较实际业绩预告增速与市场一致预期的差距。

    Args:
        ts_code: 股票代码
        data_source: 数据源（需提供 get_forecast / get_consensus 等接口）

    Returns:
        ExpectationGapResult: 预期差评分结果
    """
    all_logic: list[str] = []

    # 1. 获取实际预告增速
    actual_pct = _get_actual_forecast(ts_code, data_source)
    if actual_pct is None:
        all_logic.append("无法获取实际预告数据，预期差评分为中性")
        return ExpectationGapResult(
            score=get_config().expectation_gap.neutral_score,
            surprise_type="unknown",
            actual_pct=0.0,
            expected_pct=0.0,
            gap_pct=0.0,
            logic=all_logic,
        )

    all_logic.append(f"实际预告增速: {actual_pct:+.2f}%")

    # 2. 获取市场一致预期
    expected_pct = _get_market_consensus(ts_code, data_source)

    if expected_pct is None:
        all_logic.append("无一致预期数据，尝试估算预期增速")
        expected_pct = _estimate_expected_growth(ts_code, data_source)

    if expected_pct is None:
        all_logic.append("无法估算预期增速，预期差评分为中性")
        cfg = get_config().expectation_gap
        return ExpectationGapResult(
            score=cfg.neutral_score,
            surprise_type="unknown",
            actual_pct=actual_pct,
            expected_pct=0.0,
            gap_pct=0.0,
            logic=all_logic,
        )

    all_logic.append(f"预期增速: {expected_pct:+.2f}%")

    # 3. 计算预期差
    gap_pct, surprise_type, gap_logic = _calculate_gap(actual_pct, expected_pct)
    all_logic.extend(gap_logic)

    # 4. 映射分数
    cfg = get_config().expectation_gap
    score_map = {
        "positive": cfg.positive_surprise_score,
        "neutral": cfg.neutral_score,
        "negative": cfg.negative_surprise_score,
    }
    score = score_map.get(surprise_type, cfg.neutral_score)
    all_logic.append(f"预期差评分: {score}分（{surprise_type}）")

    return ExpectationGapResult(
        score=score,
        surprise_type=surprise_type,
        actual_pct=actual_pct,
        expected_pct=expected_pct,
        gap_pct=round(gap_pct, 2),
        logic=all_logic,
    )
