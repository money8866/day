"""
市场评分模块 — Market Multiplier

根据市场指数趋势、宽度、风险偏好判断市场状态，
返回对应市场乘数，用于调整最终评分。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from .constants import MARKET_MULTIPLIER, MarketRegime
from .models import MarketScoreResult

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 内部阈值常量
# ──────────────────────────────────────────────
_INDEX_20D_BULL_MIN: float = 3.0       # 20日涨幅 >= 3% 视为多头
_INDEX_20D_BEAR_MAX: float = -3.0      # 20日跌幅 <= -3% 视为空头
_INDEX_60D_BULL_MIN: float = 5.0
_INDEX_60D_BEAR_MAX: float = -5.0

_MA20_ABOVE_BULL_MIN: float = 60.0     # 站上 MA20 比例 >= 60%
_MA20_ABOVE_BEAR_MAX: float = 30.0     # 站上 MA20 比例 <= 30%

_BREADTH_BULL_MIN: float = 1.2         #  breadth > 1.2 多头
_BREADTH_BEAR_MAX: float = 0.8         #  breadth < 0.8 空头

_RISK_APPETITE_HIGH: float = 60.0
_RISK_APPETITE_LOW: float = 40.0

_LOOKBACK_20D: int = 20
_LOOKBACK_60D: int = 60


def _compute_index_return(
    data_source: Any,
    index_code: str = "000300.SH",
    days: int = 20,
) -> Optional[float]:
    """计算指数区间涨跌幅（%）"""
    try:
        from datetime import datetime, timedelta
        end = datetime.now().strftime("%Y%m%d")
        start_dt = datetime.now() - timedelta(days=days + 10)
        start = start_dt.strftime("%Y%m%d")

        # 尝试用 get_daily_data（ELD 标准接口）；fallback 到 daily（通用接口）
        df = None
        if hasattr(data_source, "get_daily_data"):
            prices = data_source.get_daily_data(index_code, start, end)
            if prices and len(prices) >= 2:
                closes = [p.close for p in prices]
                recent = closes[-1]
                prev = closes[0]
                return (recent - prev) / prev * 100.0
        if hasattr(data_source, "daily"):
            df = data_source.daily(ts_code=index_code, start_date=start, end_date=end)
        if df is None or df.empty:
            return None

        closes = df["close"].values
        if len(closes) < 2:
            return None

        recent = closes[-1]
        prev = closes[0]
        return (recent - prev) / prev * 100.0
    except Exception as exc:
        logger.warning("计算指数涨跌幅失败: %s", exc)
        return None


def _compute_ma20_ratio(data_source: Any) -> Optional[float]:
    """计算全市场站上 MA20 的股票比例（%）"""
    try:
        ratio = getattr(data_source, "above_ma20_ratio", None)
        if ratio is not None:
            return float(ratio)

        df = getattr(data_source, "get_stocks_above_ma20", None)
        if df is not None:
            result = df()
            if result is not None and not result.empty:
                above = result["above_ma20"].sum()
                total = len(result)
                return above / total * 100.0 if total > 0 else None

        return None
    except Exception as exc:
        logger.warning("计算MA20比例失败: %s", exc)
        return None


def _compute_market_breadth(data_source: Any) -> Optional[float]:
    """计算市场宽度（上涨家数/下跌家数）"""
    try:
        breadth = getattr(data_source, "market_breadth", None)
        if breadth is not None:
            return float(breadth)

        df = getattr(data_source, "get_market_breadth", None)
        if df is not None:
            result = df()
            if result is not None and not result.empty:
                advancers = result.get("advancers", [0])[-1]
                decliners = result.get("decliners", [1])[-1]
                return advancers / max(decliners, 1)
        return None
    except Exception as exc:
        logger.warning("计算市场宽度失败: %s", exc)
        return None


def _compute_risk_appetite(data_source: Any) -> float:
    """估算市场风险偏好（0-100）"""
    try:
        appetite = getattr(data_source, "risk_appetite", None)
        if appetite is not None:
            return float(appetite)

        # 用全市场换手率与涨停比例作为风险偏好代理
        turnover = getattr(data_source, "market_turnover", None)
        if turnover is not None:
            # 换手率越高，风险偏好越高，映射到 0-100
            t = float(turnover)
            if t > 3.0:
                return 80.0
            elif t > 2.0:
                return 60.0
            elif t > 1.0:
                return 40.0
            else:
                return 20.0

        return 50.0
    except Exception as exc:
        logger.warning("计算风险偏好失败: %s", exc)
        return 50.0


def _determine_regime(
    index_20d: Optional[float],
    index_60d: Optional[float],
    ma20_ratio: Optional[float],
    breadth: Optional[float],
    risk_appetite: float,
) -> tuple[MarketRegime, list[str]]:
    """综合多维度判断市场状态"""
    logic: list[str] = []
    signals: list[str] = []

    # —— 指数趋势信号 ——
    if index_20d is not None:
        if index_20d >= _INDEX_20D_BULL_MIN:
            signals.append(f"index_20d_bull({index_20d:+.1f}%)")
        elif index_20d <= _INDEX_20D_BEAR_MAX:
            signals.append(f"index_20d_bear({index_20d:+.1f}%)")
        else:
            signals.append(f"index_20d_neutral({index_20d:+.1f}%)")

    if index_60d is not None:
        if index_60d >= _INDEX_60D_BULL_MIN:
            signals.append(f"index_60d_bull({index_60d:+.1f}%)")
        elif index_60d <= _INDEX_60D_BEAR_MAX:
            signals.append(f"index_60d_bear({index_60d:+.1f}%)")
        else:
            signals.append(f"index_60d_neutral({index_60d:+.1f}%)")

    # —— MA20 占比信号 ——
    if ma20_ratio is not None:
        if ma20_ratio >= _MA20_ABOVE_BULL_MIN:
            signals.append(f"ma20_above_strong({ma20_ratio:.1f}%)")
        elif ma20_ratio <= _MA20_ABOVE_BEAR_MAX:
            signals.append(f"ma20_above_weak({ma20_ratio:.1f}%)")
        else:
            signals.append(f"ma20_above_moderate({ma20_ratio:.1f}%)")

    # —— 宽度信号 ——
    if breadth is not None:
        if breadth >= _BREADTH_BULL_MIN:
            signals.append(f"breadth_bull({breadth:.2f})")
        elif breadth <= _BREADTH_BEAR_MAX:
            signals.append(f"breadth_bear({breadth:.2f})")
        else:
            signals.append(f"breadth_neutral({breadth:.2f})")

    # —— 风险偏好 ——
    if risk_appetite >= _RISK_APPETITE_HIGH:
        signals.append(f"risk_appetite_high({risk_appetite:.0f})")
    elif risk_appetite <= _RISK_APPETITE_LOW:
        signals.append(f"risk_appetite_low({risk_appetite:.0f})")
    else:
        signals.append(f"risk_appetite_moderate({risk_appetite:.0f})")

    # —— 综合判断 ——
    bull_score = 0
    bear_score = 0

    if index_20d is not None:
        if index_20d >= _INDEX_20D_BULL_MIN:
            bull_score += 2
        elif index_20d <= _INDEX_20D_BEAR_MAX:
            bear_score += 2

    if index_60d is not None:
        if index_60d >= _INDEX_60D_BULL_MIN:
            bull_score += 2
        elif index_60d <= _INDEX_60D_BEAR_MAX:
            bear_score += 2

    if ma20_ratio is not None:
        if ma20_ratio >= _MA20_ABOVE_BULL_MIN:
            bull_score += 2
        elif ma20_ratio <= _MA20_ABOVE_BEAR_MAX:
            bear_score += 2
        else:
            bull_score += 1

    if breadth is not None:
        if breadth >= _BREADTH_BULL_MIN:
            bull_score += 1
        elif breadth <= _BREADTH_BEAR_MAX:
            bear_score += 1

    if risk_appetite >= _RISK_APPETITE_HIGH:
        bull_score += 1
    elif risk_appetite <= _RISK_APPETITE_LOW:
        bear_score += 1

    total = bull_score + bear_score
    if total == 0:
        regime = MarketRegime.UNKNOWN
        logic.append("信号不足，无法判断市场状态")
    elif bull_score >= bear_score * 1.5 and bull_score >= 3:
        if index_60d is not None and index_60d >= _INDEX_60D_BULL_MIN:
            regime = MarketRegime.BULL
        else:
            regime = MarketRegime.RECOVERY
    elif bear_score >= bull_score * 1.5 and bear_score >= 3:
        regime = MarketRegime.BEAR
    elif bull_score > bear_score:
        regime = MarketRegime.RECOVERY
    else:
        regime = MarketRegime.WEAK

    logic.append(f"综合信号: bull={bull_score}, bear={bear_score} → {regime.value}")
    logic.append(f"信号明细: {'; '.join(signals)}")

    return regime, logic


def get_market_score(data_source: Any) -> MarketScoreResult:
    """获取市场评分与乘数

    Args:
        data_source: 数据源对象（需提供 daily / market_breadth 等接口）

    Returns:
        MarketScoreResult: 包含市场状态、乘数、评分
    """
    # 收集各维度数据
    index_20d = _compute_index_return(data_source, days=_LOOKBACK_20D)
    index_60d = _compute_index_return(data_source, days=_LOOKBACK_60D)
    ma20_ratio = _compute_ma20_ratio(data_source)
    breadth = _compute_market_breadth(data_source)
    risk_appetite = _compute_risk_appetite(data_source)

    # 判断市场状态
    regime, logic = _determine_regime(
        index_20d=index_20d,
        index_60d=index_60d,
        ma20_ratio=ma20_ratio,
        breadth=breadth,
        risk_appetite=risk_appetite,
    )

    multiplier = MARKET_MULTIPLIER.get(regime, MARKET_MULTIPLIER[MarketRegime.UNKNOWN])

    # 评分映射：多头市场高分，空头市场低分
    regime_score_map: dict[MarketRegime, float] = {
        MarketRegime.BULL: 85.0,
        MarketRegime.RECOVERY: 65.0,
        MarketRegime.WEAK: 40.0,
        MarketRegime.BEAR: 20.0,
        MarketRegime.UNKNOWN: 50.0,
    }
    score = regime_score_map.get(regime, 50.0)

    return MarketScoreResult(
        regime=regime,
        multiplier=multiplier,
        score=score,
        risk_appetite=risk_appetite,
        logic=logic,
    )
