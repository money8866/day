# -*- coding: utf-8 -*-
"""
资金一致性评分 (Capital Consistency Score) — 权重 15%

衡量资金是否持续流入主题。
1. 成交额趋势 (40%): 20日均额/60日均额 → 增加:40 / 稳定:20 / 下降:0
2. 资金流向 (30%): 近20日净流入天数 → >15:30 / 10-15:20 / <10:0
3. 换手健康度 (30%): 健康换手:30 / 过低:10 / 过高:0
"""
import numpy as np
import pandas as pd


def calculate_capital_consistency(etf_df: pd.DataFrame,
                                   stock_data: dict = None) -> dict:
    """
    计算资金一致性评分

    Args:
        etf_df: ETF日线数据, 需含 amount 或 vol 列
        stock_data: {ts_code: DataFrame} 成份股日线 (可选, 用于辅助计算)

    Returns:
        {'score': 0-100, 'amount_trend': 0-40, 'flow_consistency': 0-30,
         'turnover_health': 0-30, 'details': {...}}
    """
    if etf_df is None or len(etf_df) < 60:
        return _default_result()

    # 优先用ETF的amount, 其次用vol
    if 'amount' in etf_df.columns:
        amount = etf_df['amount'].astype(float).reset_index(drop=True)
    elif 'vol' in etf_df.columns:
        amount = etf_df['vol'].astype(float).reset_index(drop=True)
    else:
        return _default_result()

    if len(amount) < 60:
        return _default_result()

    # === 1. 成交额趋势 (满分40) ===
    avg_20d = amount.tail(20).mean()
    avg_60d = amount.tail(60).mean()
    amount_ratio = avg_20d / (avg_60d + 1e-6) if avg_60d > 0 else 1.0

    if amount_ratio > 1.2:  # 增加20%以上
        amount_trend_score = 40
    elif amount_ratio > 0.9:  # 稳定
        amount_trend_score = 20
    else:  # 下降
        amount_trend_score = 0

    # === 2. 资金流向 (满分30) ===
    # 近20日中, 成交额高于前5日均额的天数 = 资金流入日
    inflow_days = 0
    for i in range(-20, 0):
        if len(amount) + i < 5:
            continue
        ma5 = amount.iloc[i - 5:i].mean() if i - 5 >= -len(amount) else amount.iloc[:5].mean()
        if amount.iloc[i] > ma5:
            inflow_days += 1

    if inflow_days > 15:
        flow_score = 30
    elif inflow_days >= 10:
        flow_score = 20
    else:
        flow_score = 0

    # === 3. 换手健康度 (满分30) ===
    # 用成交额的变化率来代理换手健康度
    # 避免极端投机(成交额暴增)和无人关注(成交额极低)
    recent_amount = amount.tail(20)
    amount_std = recent_amount.std() / (recent_amount.mean() + 1e-6)  # 变异系数

    # 成交额20日均值相对60日均值的比率
    if 0.9 <= amount_ratio <= 2.0 and amount_std < 0.5:
        turnover_score = 30  # 健康换手
    elif amount_ratio > 3.0 or amount_std > 0.8:
        turnover_score = 0   # 过度投机
    elif amount_ratio < 0.5:
        turnover_score = 10  # 过低
    else:
        turnover_score = 20  # 中等

    # === 综合 ===
    total = amount_trend_score + flow_score + turnover_score
    total = max(0, min(100, total))

    return {
        'score': round(total, 2),
        'amount_trend': round(amount_trend_score, 2),
        'flow_consistency': round(flow_score, 2),
        'turnover_health': round(turnover_score, 2),
        'details': {
            'avg_amount_20d': round(float(avg_20d), 0),
            'avg_amount_60d': round(float(avg_60d), 0),
            'amount_ratio': round(float(amount_ratio), 3),
            'inflow_days_20d': inflow_days,
            'amount_cv': round(float(amount_std), 3),
        }
    }


def _default_result():
    return {
        'score': 50.0, 'amount_trend': 20.0, 'flow_consistency': 15.0,
        'turnover_health': 15.0, 'details': {}
    }
