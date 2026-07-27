"""
ELD V2 机构资金评分 (Institution Score)

从资金流向、北向资金、基金持仓等多维度评估机构资金参与度。
"""

from __future__ import annotations

from typing import Any, Optional

from .config import InstitutionScoreConfig
from .models import InstitutionScoreResult, MoneyFlowData


def score_institution(
    ts_code: str,
    data_source: Any,
    config: Optional[InstitutionScoreConfig] = None,
) -> InstitutionScoreResult:
    """计算单只股票的机构资金评分。

    从资金流向数据中提取短期/中期/长期的大单净流入比例，
    结合突破放量、北向资金、基金持仓变化等因子，加权评分。

    Args:
        ts_code: 股票代码
        data_source: 数据源对象，需有 get_moneyflow(ts_code) -> list[MoneyFlowData]
                      和 get_hk_hold(ts_code) -> list[dict] 方法
        config: 机构评分配置，默认使用全局配置

    Returns:
        机构资金评分结果
    """
    if config is None:
        from .config import get_config

        config = get_config().institution

    logic: list[str] = []

    # ── 获取资金流向数据 ─────────────────────
    moneyflow_list: list[MoneyFlowData] = data_source.get_moneyflow(ts_code)

    if not moneyflow_list:
        logic.append("无资金流向数据，评分为0")
        return InstitutionScoreResult(score=0.0, logic=logic)

    # 按日期降序排列（最新在前）
    moneyflow_list_sorted = sorted(
        moneyflow_list, key=lambda x: x.trade_date, reverse=True
    )

    # ── 1. 计算各期限大单净流入比例 ────────────
    # 净大单流入 = 大单买入 - 大单卖出
    def _net_large_inflow_ratio(days: int) -> float:
        """计算最近 days 日的净大单流入 / 总成交额"""
        subset = moneyflow_list_sorted[:days]
        if not subset:
            return 0.0
        total_buy_lg = sum(m.buy_lg_amount for m in subset)
        total_sell_lg = sum(m.sell_lg_amount for m in subset)
        total_amount = sum(m.buy_lg_amount + m.sell_lg_amount for m in subset)
        if total_amount == 0:
            return 0.0
        return (total_buy_lg - total_sell_lg) / total_amount

    def _net_large_inflow_score(ratio: float) -> float:
        """将净大单流入比例映射到 0-100 分"""
        if ratio > 0.15:
            return 100.0
        if ratio > 0.10:
            return 80.0
        if ratio > 0.05:
            return 60.0
        if ratio > 0.0:
            return 40.0
        if ratio > -0.05:
            return 20.0
        return 0.0

    short_term_flow = _net_large_inflow_ratio(5)
    mid_term_flow = _net_large_inflow_ratio(10)
    long_term_flow = _net_large_inflow_ratio(20)

    short_score = _net_large_inflow_score(short_term_flow)
    mid_score = _net_large_inflow_score(mid_term_flow)
    long_score = _net_large_inflow_score(long_term_flow)

    logic.append(
        f"近5日净大单流入比 {short_term_flow:+.2%} → {short_score}分"
    )
    logic.append(
        f"近10日净大单流入比 {mid_term_flow:+.2%} → {mid_score}分"
    )
    logic.append(
        f"近20日净大单流入比 {long_term_flow:+.2%} → {long_score}分"
    )

    # ── 2. 突破放量检测 ──────────────────────
    breakout_flow: float = 0.0
    if len(moneyflow_list_sorted) >= 10:
        # 对比近5日平均 vs 前5日平均
        recent_5 = moneyflow_list_sorted[:5]
        prev_5 = moneyflow_list_sorted[5:10]

        def _avg_amount(data: list[MoneyFlowData]) -> float:
            if not data:
                return 0.0
            return sum(m.buy_lg_amount + m.sell_lg_amount for m in data) / len(data)

        recent_avg = _avg_amount(recent_5)
        prev_avg = _avg_amount(prev_5)

        if prev_avg > 0 and recent_avg > prev_avg * 1.5:
            # 成交量放大 + 净流入为正 = 突破放量
            net_5 = sum(
                m.buy_lg_amount - m.sell_lg_amount for m in recent_5
            )
            if net_5 > 0:
                breakout_flow = 100.0
                logic.append("近5日成交量放大且大单净流入 → 突破放量信号: 100分")
            else:
                breakout_flow = 50.0
                logic.append("近5日成交量放大但大单净流出 → 放量滞涨: 50分")
        else:
            breakout_flow = 30.0
            logic.append("无明显突破放量信号: 30分")
    else:
        breakout_flow = 0.0
        logic.append("数据不足，无法检测突破放量: 0分")

    # ── 3. 北向资金 ─────────────────────────
    north_flow: float = 0.0
    try:
        hk_hold_data = data_source.get_hk_hold(ts_code)
        if hk_hold_data and len(hk_hold_data) >= 2:
            # 对比最新 vs 前一周的持仓市值变化
            latest = hk_hold_data[0]
            prev = hk_hold_data[-1]
            latest_vol = getattr(latest, "vol", None) or latest.get("vol", 0)
            prev_vol = getattr(prev, "vol", None) or prev.get("vol", 0)
            if prev_vol and prev_vol > 0:
                change_ratio = (latest_vol - prev_vol) / prev_vol
                if change_ratio > 0.05:
                    north_flow = 100.0
                elif change_ratio > 0.02:
                    north_flow = 80.0
                elif change_ratio > 0.0:
                    north_flow = 60.0
                elif change_ratio > -0.02:
                    north_flow = 40.0
                else:
                    north_flow = 20.0
                logic.append(
                    f"北向持仓变化 {change_ratio:+.2%} → {north_flow}分"
                )
            else:
                north_flow = 50.0
                logic.append("北向持仓数据不足，取中性分: 50分")
        else:
            north_flow = 50.0
            logic.append("北向数据不足，取中性分: 50分")
    except (AttributeError, NotImplementedError):
        north_flow = 50.0
        logic.append("北向数据接口不可用，取中性分: 50分")

    # ── 4. 基金持仓变化 ─────────────────────
    fund_holding_change: float = 0.0
    try:
        fund_hold_data = data_source.get_fund_hold(ts_code)
        if fund_hold_data and len(fund_hold_data) >= 2:
            latest = fund_hold_data[0]
            prev = fund_hold_data[-1]
            latest_ratio = getattr(latest, "hold_ratio", None) or latest.get(
                "hold_ratio", 0
            )
            prev_ratio = getattr(prev, "hold_ratio", None) or prev.get(
                "hold_ratio", 0
            )
            if prev_ratio and prev_ratio > 0:
                change = latest_ratio - prev_ratio
                if change > 2.0:
                    fund_holding_change = 100.0
                elif change > 1.0:
                    fund_holding_change = 80.0
                elif change > 0.0:
                    fund_holding_change = 60.0
                elif change > -1.0:
                    fund_holding_change = 40.0
                else:
                    fund_holding_change = 20.0
                logic.append(
                    f"基金持仓比例变化 {change:+.2f}% → {fund_holding_change}分"
                )
            else:
                fund_holding_change = 50.0
                logic.append("基金持仓数据不足，取中性分: 50分")
        else:
            fund_holding_change = 50.0
            logic.append("基金持仓数据不足，取中性分: 50分")
    except (AttributeError, NotImplementedError):
        fund_holding_change = 50.0
        logic.append("基金持仓接口不可用，取中性分: 50分")

    # ── 5. 连续净流入天数 ───────────────────
    consecutive_inflow_days: int = 0
    for m in moneyflow_list_sorted:
        net = m.buy_lg_amount - m.sell_lg_amount
        if net > 0:
            consecutive_inflow_days += 1
        else:
            break
    logic.append(f"连续大单净流入天数: {consecutive_inflow_days}")

    # ── 6. 加权求和 ─────────────────────────
    raw_score = (
        short_score * config.short_term_weight
        + mid_score * config.mid_term_weight
        + long_score * config.long_term_weight
        + breakout_flow * config.breakout_weight
        + north_flow * config.north_flow_weight
        + fund_holding_change * config.fund_holding_weight
    )

    # 连续净流入加分（每5天加1分，上限5分）
    inflow_bonus = min(consecutive_inflow_days // 5, 5) * 1.0
    raw_score += inflow_bonus
    if inflow_bonus > 0:
        logic.append(f"连续净流入加分: +{inflow_bonus}分")

    final_score = max(0.0, min(100.0, raw_score))

    logic.append(
        f"加权得分={raw_score:.2f}, 最终={final_score:.2f}"
    )

    return InstitutionScoreResult(
        score=round(final_score, 2),
        short_term_flow=round(short_term_flow, 4),
        mid_term_flow=round(mid_term_flow, 4),
        long_term_flow=round(long_term_flow, 4),
        breakout_flow=round(breakout_flow, 2),
        north_flow=round(north_flow, 2),
        fund_holding_change=round(fund_holding_change, 2),
        consecutive_inflow_days=consecutive_inflow_days,
        logic=logic,
    )
