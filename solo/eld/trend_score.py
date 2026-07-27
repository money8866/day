"""
ELD V2 趋势评分 (Trend Score)

从均线排列、动量、波动率、相对强弱等技术面维度评估股票趋势质量。
"""

from __future__ import annotations

import math
from typing import Any, Optional

from .config import TrendScoreConfig
from .models import DailyPriceData, TrendScoreResult


def _calc_sma(prices: list[float]) -> float:
    """简单算术平均"""
    if not prices:
        return 0.0
    return sum(prices) / len(prices)


def _calc_ema(prices: list[float], period: int) -> list[float]:
    """指数移动平均"""
    if len(prices) < period:
        return []
    multiplier = 2.0 / (period + 1)
    ema = [_calc_sma(prices[:period])]
    for i in range(period, len(prices)):
        ema.append((prices[i] - ema[-1]) * multiplier + ema[-1])
    return ema


def _get_prices(data: list[DailyPriceData]) -> list[float]:
    """提取收盘价列表（按日期升序）"""
    return [d.close for d in data]


def _get_returns(data: list[float]) -> list[float]:
    """计算日收益率序列"""
    if len(data) < 2:
        return []
    return [(data[i] - data[i - 1]) / data[i - 1] for i in range(1, len(data))]


def _calc_covariance(x: list[float], y: list[float]) -> float:
    """计算两个序列的协方差"""
    if len(x) != len(y) or len(x) < 2:
        return 0.0
    n = len(x)
    mean_x = _calc_sma(x)
    mean_y = _calc_sma(y)
    return sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n)) / (n - 1)


def _calc_variance(x: list[float]) -> float:
    """计算方差"""
    if len(x) < 2:
        return 0.0
    n = len(x)
    mean_x = _calc_sma(x)
    return sum((v - mean_x) ** 2 for v in x) / (n - 1)


def _calc_atr(data: list[DailyPriceData], period: int = 14) -> float:
    """计算平均真实波幅 (ATR)"""
    if len(data) < period + 1:
        return 0.0
    tr_list: list[float] = []
    for i in range(1, len(data)):
        high_low = data[i].high - data[i].low
        high_pc = abs(data[i].high - data[i - 1].close)
        low_pc = abs(data[i].low - data[i - 1].close)
        tr_list.append(max(high_low, high_pc, low_pc))
    if len(tr_list) < period:
        return 0.0
    return _calc_sma(tr_list[-period:])


def _calc_ma_alignment_score(
    prices: list[float],
    periods: list[int] = None,
) -> tuple[float, int]:
    """计算均线排列分数。

    检查各周期均线是否呈多头排列（短期 > 长期）。
    Returns:
        (分数 0-100, 正确排列的均线对数)
    """
    if periods is None:
        periods = [5, 10, 20, 60]
    if len(prices) < max(periods) + 1:
        return 0.0, 0

    mas: list[float] = []
    for p in periods:
        ma = _calc_sma(prices[-p:])
        mas.append(ma)

    correct_pairs = 0
    total_pairs = len(mas) - 1
    for i in range(total_pairs):
        if mas[i] > mas[i + 1]:
            correct_pairs += 1

    if total_pairs == 0:
        return 0.0, 0

    score = (correct_pairs / total_pairs) * 100.0
    return score, correct_pairs


def score_trend(
    ts_code: str,
    daily_data: list[DailyPriceData],
    data_source: Any,
    config: Optional[TrendScoreConfig] = None,
) -> TrendScoreResult:
    """计算单只股票的趋势评分。

    综合 alpha、相对 alpha、均线排列、动量、波动率、
    beta、相对强弱等因子，加权评估趋势质量。

    Args:
        ts_code: 股票代码
        daily_data: 日线价格数据列表（需包含至少60个交易日）
        data_source: 数据源对象，可提供基准/行业数据
        config: 趋势评分配置，默认使用全局配置

    Returns:
        趋势评分结果
    """
    if config is None:
        from .config import get_config

        config = get_config().trend

    logic: list[str] = []

    # ── 数据预处理 ──────────────────────────
    if len(daily_data) < 10:
        logic.append("日线数据不足10个交易日，评分为0")
        return TrendScoreResult(score=0.0, logic=logic)

    # 按日期升序排列
    data_sorted = sorted(daily_data, key=lambda x: x.trade_date)
    prices = _get_prices(data_sorted)
    closes = prices
    returns_daily = _get_returns(closes)

    # 最新收盘价
    latest_close = closes[-1] if closes else 0.0
    prev_close = closes[-2] if len(closes) >= 2 else latest_close

    # 基准收益数据（尝试从 data_source 获取）
    benchmark_returns: list[float] = []
    try:
        benchmark_data = data_source.get_benchmark_daily(ts_code)
        if benchmark_data:
            benchmark_prices = _get_prices(benchmark_data)
            benchmark_returns = _get_returns(benchmark_prices)
    except (AttributeError, NotImplementedError):
        pass

    # ── 1. Alpha: 近期超额收益 ─────────────
    # 近20日 vs 近60日收益差，衡量近期相对基准的超额
    alpha: float = 0.0
    if len(closes) >= 20:
        ret_20d = (closes[-1] - closes[-21]) / closes[-21]
        if benchmark_returns and len(benchmark_returns) >= 20:
            bench_ret_20d = (
                benchmark_returns[-1]
                if len(benchmark_returns) >= 1
                else 0.0
            )
            # 如果 benchmark 数据是收益率序列，需计算20日累计
            b_ret = sum(benchmark_returns[-20:]) if len(benchmark_returns) >= 20 else 0.0
            alpha = ret_20d - b_ret
        else:
            alpha = ret_20d
    else:
        alpha = (closes[-1] - closes[0]) / closes[0] if closes[0] > 0 else 0.0

    if alpha > 0.20:
        alpha_score = 100.0
    elif alpha > 0.10:
        alpha_score = 80.0
    elif alpha > 0.05:
        alpha_score = 60.0
    elif alpha > 0.0:
        alpha_score = 40.0
    elif alpha > -0.10:
        alpha_score = 20.0
    else:
        alpha_score = 0.0
    logic.append(f"Alpha (超额收益) {alpha:+.2%} → {alpha_score}分")

    # ── 2. 相对 Alpha: 相对行业收益 ─────────
    relative_alpha: float = 0.0
    try:
        industry_data = data_source.get_industry_daily(ts_code)
        if industry_data:
            ind_prices = _get_prices(industry_data)
            if len(ind_prices) >= 20 and len(closes) >= 20:
                stock_ret = (closes[-1] - closes[-21]) / closes[-21]
                ind_ret = (
                    ind_prices[-1] - ind_prices[-21]
                ) / ind_prices[-21]
                relative_alpha = stock_ret - ind_ret
        else:
            relative_alpha = 0.0
    except (AttributeError, NotImplementedError):
        relative_alpha = 0.0

    if relative_alpha > 0.15:
        relative_alpha_score = 100.0
    elif relative_alpha > 0.08:
        relative_alpha_score = 80.0
    elif relative_alpha > 0.03:
        relative_alpha_score = 60.0
    elif relative_alpha > 0.0:
        relative_alpha_score = 40.0
    elif relative_alpha > -0.08:
        relative_alpha_score = 20.0
    else:
        relative_alpha_score = 0.0
    logic.append(f"相对行业Alpha {relative_alpha:+.2%} → {relative_alpha_score}分")

    # ── 3. Trend: 均线多头排列 ─────────────
    trend_score_val, correct_pairs = _calc_ma_alignment_score(closes)
    logic.append(
        f"均线排列 {correct_pairs}/3 对多头 → {trend_score_val}分"
    )

    # ── 4. Momentum: 20日价格变化率 ─────────
    momentum: float = 0.0
    if len(closes) >= 21:
        momentum = (closes[-1] - closes[-21]) / closes[-21]
    else:
        momentum = (closes[-1] - closes[0]) / closes[0] if closes[0] > 0 else 0.0

    if momentum > 0.30:
        momentum_score = 100.0
    elif momentum > 0.15:
        momentum_score = 85.0
    elif momentum > 0.08:
        momentum_score = 65.0
    elif momentum > 0.0:
        momentum_score = 45.0
    elif momentum > -0.10:
        momentum_score = 25.0
    else:
        momentum_score = 5.0
    logic.append(f"20日动量 {momentum:+.2%} → {momentum_score}分")

    # ── 5. MA Alignment: 均线排列完整度 ─────
    ma_alignment = trend_score_val  # 复用均线排列分
    logic.append(f"均线对齐度: {ma_alignment}分")

    # ── 6. New High Count: 近60日内创20日新高的次数 ──
    new_high_count: int = 0
    lookback = min(60, len(closes))
    new_high_window = 20
    if lookback > new_high_window:
        for i in range(lookback - new_high_window, lookback):
            window_high = max(closes[i - new_high_window : i]) if i >= new_high_window else 0
            if i >= new_high_window and closes[i] > window_high:
                new_high_count += 1

    new_high_score = min(new_high_count * 10, 100)
    logic.append(f"近60日创20日新高次数 {new_high_count} → {new_high_score}分")

    # ── 7. ATR Ratio: 波动幅度 ─────────────
    atr_val = _calc_atr(data_sorted, 14)
    atr_ratio = atr_val / latest_close if latest_close > 0 else 0.0

    # 适中的 ATR 比率最好（0.01~0.04）
    if atr_ratio <= 0.005:
        atr_score = 30.0  # 过于平淡
    elif atr_ratio <= 0.015:
        atr_score = 70.0  # 低波动，趋势股特征
    elif atr_ratio <= 0.03:
        atr_score = 90.0  # 理想波动
    elif atr_ratio <= 0.05:
        atr_score = 60.0  # 偏高波动
    else:
        atr_score = 30.0  # 极高波动，风险大
    logic.append(f"ATR比率 {atr_ratio:.4f} → {atr_score}分")

    # ── 8. Volatility: 20日收益标准差 ────────
    volatility: float = 0.0
    vol_window = min(20, len(returns_daily))
    if vol_window >= 5:
        recent_returns = returns_daily[-vol_window:]
        volatility = (
            sum((r - _calc_sma(recent_returns)) ** 2 for r in recent_returns)
            / (len(recent_returns) - 1)
        ) ** 0.5

    # 低波动 = 高分
    if volatility <= 0.01:
        volatility_score = 80.0
    elif volatility <= 0.02:
        volatility_score = 90.0  # 理想低波动
    elif volatility <= 0.03:
        volatility_score = 70.0
    elif volatility <= 0.05:
        volatility_score = 50.0
    else:
        volatility_score = 20.0  # 高波动
    logic.append(f"20日波动率 {volatility:.4f} → {volatility_score}分")

    # ── 9. Beta: 相对基准的弹性 ─────────────
    beta: float = 0.0
    beta_window = min(60, len(returns_daily), len(benchmark_returns))
    if beta_window >= 10:
        stock_ret_slice = returns_daily[-beta_window:]
        bench_ret_slice = benchmark_returns[-beta_window:]
        if len(stock_ret_slice) == len(bench_ret_slice):
            cov = _calc_covariance(stock_ret_slice, bench_ret_slice)
            var = _calc_variance(bench_ret_slice)
            if var > 0:
                beta = cov / var

    # Beta 接近 1 最好（与市场同步），0.8~1.5 可接受
    if 0.8 <= beta <= 1.2:
        beta_score = 100.0
    elif 1.2 < beta <= 1.5 or 0.5 <= beta < 0.8:
        beta_score = 70.0
    elif 1.5 < beta <= 2.0:
        beta_score = 50.0
    elif 0.0 <= beta < 0.5:
        beta_score = 40.0
    else:
        beta_score = 20.0  # 负 beta 或极高 beta
    logic.append(f"Beta {beta:.2f} → {beta_score}分")

    # ── 10. Relative Strength: 20日相对强弱 ──
    relative_strength: float = 0.0
    rs_window = 20
    if len(closes) >= rs_window + 1 and len(benchmark_returns) >= rs_window:
        stock_ret_rs = (closes[-1] - closes[-rs_window - 1]) / closes[-rs_window - 1]
        bench_ret_rs = sum(benchmark_returns[-rs_window:])
        if bench_ret_rs != 0:
            relative_strength = stock_ret_rs / abs(bench_ret_rs)
        else:
            relative_strength = 1.0 if stock_ret_rs > 0 else -1.0
    else:
        relative_strength = 0.0

    if relative_strength > 2.0:
        rs_score = 100.0
    elif relative_strength > 1.5:
        rs_score = 85.0
    elif relative_strength > 1.0:
        rs_score = 65.0
    elif relative_strength > 0.5:
        rs_score = 45.0
    elif relative_strength > 0.0:
        rs_score = 25.0
    else:
        rs_score = 5.0
    logic.append(f"20日相对强度 {relative_strength:.2f} → {rs_score}分")

    # ── 加权求和 ─────────────────────────
    raw_score = (
        alpha_score * config.alpha_weight
        + relative_alpha_score * config.relative_alpha_weight
        + trend_score_val * config.trend_weight
        + momentum_score * config.momentum_weight
        + ma_alignment * config.ma_alignment_weight
        + new_high_score * config.new_high_count_weight
        + atr_score * config.atr_weight
        + volatility_score * config.volatility_weight
        + beta_score * config.beta_weight
        + rs_score * config.relative_strength_weight
    )

    final_score = max(0.0, min(100.0, raw_score))

    # 均线排列完整度加分（5对均线全部多头排列时额外加分）
    # 这里只用了4个周期（5/10/20/60），所以3/3全对额外加分
    if correct_pairs >= 3 and len(closes) >= 60:
        bonus = 5.0
        final_score = max(0.0, min(100.0, final_score + bonus))
        logic.append(f"均线全部多头排列奖励: +{bonus}分")

    logic.append(f"加权得分={raw_score:.2f}, 最终={final_score:.2f}")

    return TrendScoreResult(
        score=round(final_score, 2),
        alpha=round(alpha, 4),
        relative_alpha=round(relative_alpha, 4),
        trend=round(trend_score_val, 2),
        momentum=round(momentum, 4),
        ma_alignment=round(ma_alignment, 2),
        new_high_count=new_high_count,
        atr_ratio=round(atr_ratio, 4),
        volatility=round(volatility, 4),
        beta=round(beta, 4),
        relative_strength=round(relative_strength, 4),
        logic=logic,
    )
