# -*- coding: utf-8 -*-
"""仓位控制系统 - 状态+风险双因子"""
from config import POSITION

def calc_position(market_state, divergence_result, current_holdings=None):
    """
    计算建议仓位
    market_state: from market_state.classify_market_state()
    divergence_result: from divergence.check_divergence()
    current_holdings: list of held stock codes
    """
    state = market_state.get("state", "recover")

    # 基准仓位
    base = POSITION["state_base"].get(state, 0.40)

    # 分歧折价
    risk_level = divergence_result.get("risk_level", 0)
    if risk_level >= 3:
        discount = 0.3
    elif risk_level >= 2:
        discount = POSITION["divergence_discount"]
    elif risk_level >= 1:
        discount = 0.8
    else:
        discount = 1.0

    target = base * discount

    # 当前持仓数量限制
    hold_count = len(current_holdings) if current_holdings else 0
    max_stocks = POSITION["max_stocks"]
    can_add = max(0, max_stocks - hold_count)

    # 单只最大仓位
    single = min(POSITION["single_max"], target / max(1, hold_count + 1))

    return {
        "state": state,
        "base_position": base,
        "divergence_discount": discount,
        "target_position": target,
        "can_add_stocks": can_add,
        "single_max": single,
        "max_stocks": max_stocks,
        "summary": (
            f"市场{state} → 基准{base:.0%} × 分歧系数{discount:.0%} "
            f"= 目标{target:.0%} | 可加{can_add}只 | 单只≤{single:.0%}"
        ),
    }

def check_stop_conditions(holdings_df):
    """
    检查止损/止盈/移动止损条件
    holdings_df: DataFrame with columns [code, name, buy_price, current_price, high_since_buy]
    返回: list of action dicts
    """
    actions = []
    if holdings_df is None or len(holdings_df) == 0:
        return actions

    for _, row in holdings_df.iterrows():
        buy = row['buy_price']
        current = row['current_price']
        high = row.get('high_since_buy', current)
        pnl = (current - buy) / buy

        action = None

        # 止损
        if pnl <= POSITION["stop_loss"]:
            action = {"type": "stop_loss", "reason": f"止损{pnl:.1%}"}

        # 止盈
        elif pnl >= POSITION["take_profit"]:
            action = {"type": "take_profit", "reason": f"止盈{pnl:.1%}"}

        # 移动止损 (从最高点回撤超过阈值)
        drawdown = (current - high) / high
        if drawdown <= -POSITION["trailing_stop"] and pnl > 0.05:
            action = {"type": "trailing_stop",
                      "reason": f"移动止损(从高点回撤{drawdown:.1%})"}

        if action:
            actions.append({
                "code": row['code'],
                "name": row['name'],
                "buy_price": buy,
                "current_price": current,
                "pnl": pnl,
                **action,
            })

    return actions
