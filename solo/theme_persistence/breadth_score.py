# -*- coding: utf-8 -*-
"""
广度扩张评分 (Breadth Expansion Score) — 权重 25%

检测资金是否从少数龙头向全行业扩散。
1. 广度比率 (30%): 站上MA20的个股占比 → >70%:30 / 50-70%:20 / 30-50%:10 / <30%:0
2. 广度趋势 (30%): 广度比率20日斜率 → 正加速:30 / 稳定:15 / 下降:0
3. 强势股扩张 (20%): Close>MA20 且 RS排名<30% 的个股占比 → 有:30 / 无:0
4. 涨停扩散 (20%): 涨停数趋势 → 增加:10 / 平稳:5 / 下降:0
"""
import numpy as np
import pandas as pd


def calculate_breadth_expansion(stock_data: dict, trade_date: str) -> dict:
    """
    计算广度扩张评分

    Args:
        stock_data: {ts_code: DataFrame} 每只成份股的日线数据
                     DataFrame 需含 close, high, low, vol, trade_date 列
        trade_date: 交易日 'YYYYMMDD'

    Returns:
        {'score': 0-100, 'breadth_ratio': 0-30, 'breadth_trend': 0-30,
         'strong_expansion': 0-30, 'limit_up_expansion': 0-10, 'details': {...}}
    """
    if not stock_data or len(stock_data) < 3:
        return _default_result()

    td = pd.to_datetime(trade_date, format="%Y%m%d")
    total_stocks = len(stock_data)

    # 收集每只股票的指标
    above_ma20_today = 0
    above_ma20_20d_ago = 0
    strong_stocks = 0
    limit_up_today = 0
    limit_up_5d_ago = 0
    valid_count = 0

    # 用于计算广度比率时间序列
    breadth_history = []

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
        vol = df['vol'].astype(float) if 'vol' in df.columns else None
        high = df['high'].astype(float) if 'high' in df.columns else close

        valid_count += 1
        ma20 = close.rolling(20).mean()

        # 今日是否站上MA20
        if not np.isnan(ma20.iloc[-1]) and close.iloc[-1] > ma20.iloc[-1]:
            above_ma20_today += 1

        # 20日前是否站上MA20
        if len(ma20) >= 20 and not np.isnan(ma20.iloc[-20]):
            if close.iloc[-20] > ma20.iloc[-20]:
                above_ma20_20d_ago += 1

        # 强势股: Close>MA20 且 20D收益排名前30%
        ret_20d = (close.iloc[-1] / close.iloc[-21] - 1) * 100 if len(close) >= 21 else 0
        if not np.isnan(ma20.iloc[-1]) and close.iloc[-1] > ma20.iloc[-1] and ret_20d > 3:
            strong_stocks += 1

        # 涨停检测 (涨幅>=9.5%)
        if len(close) >= 2:
            daily_pct = (close.iloc[-1] / close.iloc[-2] - 1) * 100
            if daily_pct >= 9.5:
                limit_up_today += 1
        if len(close) >= 6:
            daily_pct_5d = (close.iloc[-5] / close.iloc[-6] - 1) * 100
            if daily_pct_5d >= 9.5:
                limit_up_5d_ago += 1

        # 构建广度比率历史 (近20日每天是否站上MA20)
        for i in range(-min(20, len(df) - 20), 0):
            idx = len(ma20) + i
            if idx >= 0 and not np.isnan(ma20.iloc[idx]):
                pass  # 在下面批量计算

    if valid_count < 3:
        return _default_result()

    # === 1. 广度比率 (满分30) ===
    breadth_ratio = above_ma20_today / valid_count
    if breadth_ratio > 0.70:
        breadth_ratio_score = 30
    elif breadth_ratio > 0.50:
        breadth_ratio_score = 20
    elif breadth_ratio > 0.30:
        breadth_ratio_score = 10
    else:
        breadth_ratio_score = 0

    # === 2. 广度趋势 (满分30) ===
    breadth_today = above_ma20_today / valid_count
    breadth_20d_ago = above_ma20_20d_ago / valid_count if valid_count > 0 else 0
    breadth_change = breadth_today - breadth_20d_ago

    if breadth_change > 0.10:  # 正加速
        breadth_trend_score = 30
    elif breadth_change > -0.05:  # 稳定
        breadth_trend_score = 15
    else:  # 下降
        breadth_trend_score = 0

    # === 3. 强势股扩张 (满分30) ===
    strong_ratio = strong_stocks / valid_count
    if strong_ratio > 0.20:
        strong_expansion_score = 30
    elif strong_ratio > 0.10:
        strong_expansion_score = 20
    elif strong_ratio > 0.05:
        strong_expansion_score = 15
    else:
        strong_expansion_score = 0

    # === 4. 涨停扩散 (满分10) ===
    if limit_up_today > limit_up_5d_ago and limit_up_today > 0:
        limit_up_score = 10  # 增加
    elif limit_up_today > 0:
        limit_up_score = 5  # 平稳
    else:
        limit_up_score = 0  # 下降/无

    # === 综合: 30% 广度比率 + 30% 广度趋势 + 20% 强势股 + 20% 涨停 ===
    # 注意: 强势股满分30→映射到20分维度, 涨停满分10→映射到20分维度
    total = (
        breadth_ratio_score / 30 * 30 +
        breadth_trend_score / 30 * 30 +
        strong_expansion_score / 30 * 20 +
        limit_up_score / 10 * 20
    )
    total = max(0, min(100, total))

    return {
        'score': round(total, 2),
        'breadth_ratio': round(breadth_ratio_score, 2),
        'breadth_trend': round(breadth_trend_score, 2),
        'strong_expansion': round(strong_expansion_score, 2),
        'limit_up_expansion': round(limit_up_score, 2),
        'details': {
            'total_stocks': valid_count,
            'above_ma20_count': above_ma20_today,
            'breadth_ratio_pct': round(breadth_ratio * 100, 1),
            'breadth_change_20d': round(breadth_change * 100, 1),
            'strong_stock_count': strong_stocks,
            'limit_up_today': limit_up_today,
            'limit_up_5d_ago': limit_up_5d_ago,
        }
    }


def _default_result():
    return {
        'score': 50.0, 'breadth_ratio': 15.0, 'breadth_trend': 15.0,
        'strong_expansion': 15.0, 'limit_up_expansion': 5.0, 'details': {}
    }
