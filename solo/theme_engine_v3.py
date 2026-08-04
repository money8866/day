# -*- coding: utf-8 -*-
"""
猎尾V3 - 主题资金层引擎
========================
主线主题强度评分 (30分)

评分维度:
1. 主题热度 (10分): 涨停数量 + 上涨比例
2. 龙头强度 (10分): 主题最高涨幅股票的表现
3. 资金扩散 (10分): 主题成交额相对5日均值的变化

纯函数模块,无外部依赖,可独立用于回测和盘中模式。
"""


def theme_score_v3(theme_limit_count, theme_up_ratio, leader_change, theme_amount_ratio=None,
                   theme_avg_change=None, theme_strength=None):
    """
    V3 主题资金层评分 (30分)

    参数:
        theme_limit_count: 主题内涨停股数量 (int)
        theme_up_ratio:    主题上涨家数比例 (0-100, float)
        leader_change:     主题最强龙头涨幅 (%, float)
        theme_amount_ratio: 主题成交额/5日均成交额 (float, 可选, 回测可传None)
        theme_avg_change:   主题平均涨幅 (%, float, 可选)
        theme_strength:     主题强度值 (float, 可选, 降级备用)

    返回:
        (score: int, detail: dict)
    """
    score = 0
    detail = {
        'limit_count': theme_limit_count,
        'up_ratio': round(theme_up_ratio, 1),
        'leader_change': round(leader_change, 2) if leader_change else 0,
    }

    # ── 1. 主题热度 (10分) ──
    # 涨停数量
    heat_score = 0
    if theme_limit_count >= 5:
        heat_score = 10
    elif theme_limit_count >= 3:
        heat_score = 7
    elif theme_limit_count >= 1:
        heat_score = 4
    # 上涨比例加分
    up_bonus = 0
    if theme_up_ratio >= 70:
        up_bonus = 3
    elif theme_up_ratio >= 50:
        up_bonus = 2
    heat_score = min(heat_score + up_bonus, 10)
    score += heat_score
    detail['heat_score'] = heat_score

    # ── 2. 龙头强度 (10分) ──
    leader_score = 0
    if leader_change > 8:
        leader_score = 10
    elif leader_change > 5:
        leader_score = 7
    elif leader_change > 3:
        leader_score = 5
    elif leader_change > 0:
        leader_score = 3
    # 龙头跌停大力扣分
    if leader_change <= -9.5:
        leader_score = -10
    score += leader_score
    detail['leader_score'] = leader_score

    # ── 3. 资金扩散 (10分) ──
    diffusion_score = 0
    if theme_amount_ratio is not None:
        if theme_amount_ratio >= 1.5:
            diffusion_score = 10
        elif theme_amount_ratio >= 1.2:
            diffusion_score = 7
        elif theme_amount_ratio >= 1.0:
            diffusion_score = 5
        elif theme_amount_ratio >= 0.8:
            diffusion_score = 3
    else:
        # 回测模式降级: 用平均涨幅和强度近似
        if theme_avg_change is not None and theme_strength is not None:
            if theme_avg_change > 2 and theme_strength > 1.5:
                diffusion_score = 7
            elif theme_avg_change > 1 and theme_strength > 0.5:
                diffusion_score = 5
            elif theme_avg_change > 0:
                diffusion_score = 3
        else:
            diffusion_score = 5  # 降级默认中等
    score += diffusion_score
    detail['diffusion_score'] = diffusion_score
    detail['amount_ratio'] = round(theme_amount_ratio, 2) if theme_amount_ratio is not None else None

    total = max(0, score)
    detail['v3_total'] = min(total, 30)
    return min(total, 30), detail