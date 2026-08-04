# -*- coding: utf-8 -*-
"""
猎尾V3 - 个股资金行为引擎
==========================
个股资金行为评分 (25分)

评分维度:
1. 成交额异常 (10分): 今日成交额/20日平均成交额
2. 换手质量 (8分):  换手率区间质量
3. 主力资金代理 (7分): 大单净流入/成交集中度/尾盘成交占比

纯函数模块,无外部依赖,可独立用于回测和盘中模式。
"""


def capital_score_v3(ts_code, q, kline, turnover, snap=None, moneyflow=None):
    """
    V3 个股资金行为评分 (25分)

    参数:
        ts_code:   股票代码
        q:         行情数据 {price, pct_chg, vol, amount, open, high, low, ...}
        kline:     历史K线 DataFrame (columns: close, vol, amount, ...)
        turnover:  换手率 (%)
        snap:      分时快照 dict(tail_vol_ratio, tail_base_price, ...) (可选)
        moneyflow: 资金流向数据 (可选, 回测模式降级用)

    返回:
        (score: int, detail: dict)
    """
    score = 0
    detail = {
        'amount_ratio': 0,
        'turnover': round(turnover, 1) if turnover else 0,
        'amount_abnormal': 0,
        'turnover_quality': 0,
        'moneyflow_proxy': 0,
    }

    vol = q.get('vol', 0)
    amount = q.get('amount', 0)

    # ── 1. 成交额异常 (10分) ──
    amount_ratio = 0
    if kline is not None and len(kline) >= 20:
        if 'amount' in kline.columns:
            avg_amount_20 = kline['amount'].iloc[-20:].mean()
        elif 'vol' in kline.columns:
            avg_vol_20 = kline['vol'].iloc[-20:].mean()
            if avg_vol_20 > 0 and vol > 0:
                amount_ratio = vol / avg_vol_20
        else:
            avg_amount_20 = 0

        if avg_amount_20 > 0 and amount > 0 and 'amount' in kline.columns:
            amount_ratio = amount / avg_amount_20

    elif kline is not None and len(kline) >= 5:
        # 数据不足时的降级: 用5日均量
        if 'amount' in kline.columns:
            avg_amount_5 = kline['amount'].iloc[-5:].mean()
            if avg_amount_5 > 0 and amount > 0:
                amount_ratio = amount / avg_amount_5

    detail['amount_ratio'] = round(amount_ratio, 2)

    abnormal_score = 0
    if amount_ratio >= 2:
        abnormal_score = 10
    elif amount_ratio >= 1.5:
        abnormal_score = 8
    elif amount_ratio >= 1.2:
        abnormal_score = 5
    elif amount_ratio >= 0.8:
        abnormal_score = 3
    detail['amount_abnormal'] = abnormal_score
    score += abnormal_score

    # ── 2. 换手质量 (8分) ──
    tq_score = 0
    if turnover > 0:
        if 5 <= turnover <= 10:
            tq_score = 8
        elif 3 <= turnover <= 12:
            tq_score = 6
        elif 12 < turnover <= 20:
            tq_score = 3
        elif turnover > 20:
            tq_score = -5
        elif turnover < 1:
            tq_score = -3
        elif 1 <= turnover < 3:
            tq_score = 3
    detail['turnover_quality'] = tq_score
    score += tq_score

    # ── 3. 主力资金代理 (7分) ──
    mf_score = 0
    mf_signals = 0

    # 3a. 大单净流入 (如果有moneyflow数据)
    if moneyflow:
        net_mf = moneyflow.get('net_mf_amount', 0) or moneyflow.get('net_amount', 0)
        if net_mf > 0:
            mf_signals += 2
    else:
        # 回测降级: 用涨幅+量比近似
        pct = q.get('pct_chg', 0)
        if amount_ratio >= 1.2 and pct > 2:
            mf_signals += 1

    # 3b. 尾盘成交占比 (如果有快照)
    tail_vol_ratio = snap.get('tail_vol_ratio', 0) if snap else 0
    if tail_vol_ratio > 0.30:
        mf_signals += 2
    elif tail_vol_ratio > 0.20:
        mf_signals += 1

    # 3c. 成交集中度: 用涨幅+阳线实体近似
    pct = q.get('pct_chg', 0)
    open_p = q.get('open', 0)
    price = q.get('price', 0)
    if open_p > 0 and price > open_p:  # 收阳线
        if pct > 3:
            mf_signals += 2
        elif pct > 1:
            mf_signals += 1

    # 综合评分
    if mf_signals >= 5:
        mf_score = 7
    elif mf_signals >= 3:
        mf_score = 4
    elif mf_signals >= 1:
        mf_score = 2

    detail['moneyflow_proxy'] = mf_score
    detail['mf_signals'] = mf_signals
    score += mf_score

    total = max(0, score)
    detail['v3_total'] = min(total, 25)
    return min(total, 25), detail