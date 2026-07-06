# -*- coding: utf-8 -*-
"""
检查ETF在20260610-20的状态
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, 'd:\\mystock\\tdx_backtest')
from data_loader import load_kline


def compute_etf_state(df_etf, etf_code, date_str):
    """复制ETF状态计算函数"""
    df = df_etf[df_etf['trade_date'] <= date_str].copy()
    if len(df) < 80:
        return None
    C = df['close'].values
    H = df['high'].values
    # 复制所需函数
    def ema(arr, n):
        return pd.Series(arr).ewm(span=n, adjust=False).mean().values
    def rolling_max(arr, n):
        return pd.Series(arr).rolling(n).max().shift(1).values

    ema20 = ema(C, 20)
    ema60 = ema(C, 60)
    if np.isnan(ema20[-1]) or np.isnan(ema60[-1]) or ema60[-1] == 0:
        return None

    trend_score = (ema20[-1] - ema60[-1]) / ema60[-1]
    momentum = C[-1] / C[-21] -1 if len(C)>=21 else 0
    momentum_40d = C[-1]/C[-41]-1 if len(C)>=41 else 0
    high_20_prev = rolling_max(H, 20)
    if np.isnan(high_20_prev[-1]):
        return None
    breakout = C[-1] > high_20_prev[-1]
    is_strong = trend_score > 0 and momentum > 0 and momentum_40d >0 and breakout
    return {
        'date': date_str,
        'trend_score': trend_score,
        'momentum': momentum,
        'momentum_40d': momentum_40d,
        'breakout': breakout,
        'is_strong': is_strong,
        'close': C[-1]
    }


# 加载两只ETF的数据
etf_codes = ['512480.SH', '159516.SZ']
for code in etf_codes:
    print(f'===== {code} =====')
    df_etf = load_kline(code, start_date='20260301', end_date='20260630')
    print('最近20行:')
    print(df_etf[['trade_date', 'open', 'high', 'low', 'close']].tail(20))

    # 检查6月10-20日
    dates = ['20260610', '20260611', '20260612', '20260615', '20260616', '20260617', '20260618']
    for d in dates:
        state = compute_etf_state(df_etf, code, d)
        if state:
            print(f'{d}: 强: {state["is_strong"]}, 趋: {state["trend_score"]:.3f}, 20日动量: {state["momentum"]:.3f}, 40日动量: {state["momentum_40d"]:.3f}, 突破: {state["breakout"]}')
        else:
            print(f'{d}: data不足')
