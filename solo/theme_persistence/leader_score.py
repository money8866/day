# -*- coding: utf-8 -*-
"""
龙头持续性评分 (Leader Persistence Score) — 权重 20%

判断主题龙头是否持续主导。
1. 龙头相对强度 (40%): Top5龙头在主题内RS百分位均值
2. 龙头趋势健康 (30%): Price>MA20 + Price>MA60 + MA20斜率向上
3. 龙头稳定性 (30%): 近60日内龙头留在Top5的天数比例
"""
import numpy as np
import pandas as pd


def calculate_leader_persistence(stock_data: dict,
                                  trade_date: str,
                                  top_n: int = 5) -> dict:
    """
    计算龙头持续性评分

    Args:
        stock_data: {ts_code: DataFrame} 每只成份股日线
        trade_date: 'YYYYMMDD'
        top_n: 识别前N名龙头

    Returns:
        {'score': 0-100, 'leader_rs': 0-40, 'leader_trend': 0-30,
         'leader_stability': 0-30, 'details': {...}}
    """
    if not stock_data or len(stock_data) < 5:
        return _default_result()

    td = pd.to_datetime(trade_date, format="%Y%m%d")

    # 计算每只股票的综合排名因子
    stock_factors = []
    for code, df in stock_data.items():
        if df is None or len(df) < 25:
            continue
        df = df.copy()
        df['trade_date'] = pd.to_datetime(df['trade_date'], format="%Y%m%d")
        df = df.sort_values('trade_date').reset_index(drop=True)
        df = df[df['trade_date'] <= td]
        if len(df) < 25:
            continue

        close = df['close'].astype(float)
        ret_20d = (close.iloc[-1] / close.iloc[-21] - 1) * 100 if len(close) >= 21 else 0
        ret_60d = (close.iloc[-1] / close.iloc[-61] - 1) * 100 if len(close) >= 61 else 0

        # 20日收益排名百分位 (用于RS)
        stock_factors.append({
            'code': code,
            'ret_20d': ret_20d,
            'ret_60d': ret_60d,
            'close': close.iloc[-1],
            'df': df,
        })

    if len(stock_factors) < 5:
        return _default_result()

    # 按 20D收益*0.4 + 60D收益*0.6 排名选出龙头
    for s in stock_factors:
        s['rank_score'] = s['ret_20d'] * 0.4 + s['ret_60d'] * 0.6

    stock_factors.sort(key=lambda x: x['rank_score'], reverse=True)
    leaders = stock_factors[:top_n]

    # 所有股票的20D收益用于计算RS百分位
    all_ret_20d = [s['ret_20d'] for s in stock_factors]
    all_ret_20d_sorted = sorted(all_ret_20d)

    # === 1. 龙头相对强度 (满分40) ===
    leader_rs_list = []
    for leader in leaders:
        # RS百分位 = 该股票在主题内的排名位置 (0-1, 1=最强)
        rank_pos = sum(1 for r in all_ret_20d if r <= leader['ret_20d']) / len(all_ret_20d)
        leader_rs_list.append(rank_pos)

    avg_leader_rs = np.mean(leader_rs_list) if leader_rs_list else 0
    # RS百分位>0.8 → 满分40; 0.6-0.8 → 30; 0.4-0.6 → 20; <0.4 → 10
    if avg_leader_rs > 0.80:
        leader_rs_score = 40
    elif avg_leader_rs > 0.60:
        leader_rs_score = 30
    elif avg_leader_rs > 0.40:
        leader_rs_score = 20
    else:
        leader_rs_score = 10

    # === 2. 龙头趋势健康 (满分30) ===
    healthy_count = 0
    for leader in leaders:
        close = leader['df']['close'].astype(float)
        if len(close) < 60:
            continue
        ma20 = close.rolling(20).mean().iloc[-1]
        ma60 = close.rolling(60).mean().iloc[-1]
        last_close = close.iloc[-1]

        conditions_met = 0
        if not np.isnan(ma20) and last_close > ma20:
            conditions_met += 1
        if not np.isnan(ma60) and last_close > ma60:
            conditions_met += 1
        if not np.isnan(ma20) and not np.isnan(ma20):
            ma20_slope = (ma20 / close.rolling(20).mean().iloc[-5] - 1) * 100
            if ma20_slope > 0:
                conditions_met += 1

        if conditions_met >= 3:
            healthy_count += 1

    leader_trend_score = (healthy_count / len(leaders)) * 30 if leaders else 0

    # === 3. 龙头稳定性 (满分30) ===
    # 近60日内龙头留在Top5的天数比例
    # 简化: 检查当前龙头在20日/40日/60日前是否也是Top5
    stability_checks = 0
    total_checks = 0
    for lookback in [20, 40, 60]:
        past_factors = []
        for s in stock_factors:
            df = s['df']
            close = df['close'].astype(float)
            idx = len(close) - 1 - lookback
            if idx < 21:
                continue
            ret_20d = (close.iloc[idx] / close.iloc[idx - 21] - 1) * 100
            ret_60d = (close.iloc[idx] / close.iloc[idx - 61] - 1) * 100 if idx >= 61 else 0
            past_factors.append({'code': s['code'], 'rank_score': ret_20d * 0.4 + ret_60d * 0.6})

        if len(past_factors) >= 5:
            past_factors.sort(key=lambda x: x['rank_score'], reverse=True)
            past_top5_codes = set(x['code'] for x in past_factors[:top_n])
            current_top5_codes = set(l['code'] for l in leaders)
            overlap = len(past_top5_codes & current_top5_codes)
            stability_checks += overlap / top_n
            total_checks += 1

    leader_stability = (stability_checks / total_checks * 30) if total_checks > 0 else 15

    # === 综合 ===
    total = leader_rs_score + leader_trend_score + leader_stability
    total = max(0, min(100, total))

    return {
        'score': round(total, 2),
        'leader_rs': round(leader_rs_score, 2),
        'leader_trend': round(leader_trend_score, 2),
        'leader_stability': round(leader_stability, 2),
        'details': {
            'top_leaders': [l['code'] for l in leaders],
            'avg_leader_rs': round(float(avg_leader_rs), 3),
            'healthy_leaders': healthy_count,
            'total_leaders': len(leaders),
            'stability_ratio': round(float(stability_checks / total_checks), 3) if total_checks > 0 else 0,
        }
    }


def _default_result():
    return {
        'score': 50.0, 'leader_rs': 20.0, 'leader_trend': 15.0,
        'leader_stability': 15.0, 'details': {}
    }
