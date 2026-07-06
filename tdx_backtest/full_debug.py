# -*- coding: utf-8 -*-
"""
完整debug600206的过程
"""
import sys
import pandas as pd
import numpy as np

sys.path.insert(0, r"D:\mystock\tdx_backtest")

from data_loader import load_kline
from etf_breakout_strategy import get_board, BoardConfig


def load_all_data():
    """加载所需数据"""
    print('加载ETF和成份股...')
    # 先从缓存加载ETF成份股
    import json
    cache_file = r"D:\mystock\cache_daily\etf_constituents_all.json"
    with open(cache_file, 'r', encoding='utf-8') as f:
        etf_constituents = json.load(f)

    # 找到包含600206的ETF
    my_etfs = []
    for etf, cons in etf_constituents.items():
        if '600206.SH' in cons:
            print(f'找到ETF: {etf}包含600206')
            my_etfs.append(etf)

    print(f'共找到{len(my_etfs)}只ETF')
    # 加载这些ETF的数据
    etf_data = {}
    for etf in my_etfs:
        df = load_kline(etf, start_date='20260101', end_date='20260731')
        if len(df) >= 40:
            etf_data[etf] = df
        else:
            print(f'{etf}数据不足，只有{len(df)}条')
    # 加载600206
    stock_data = load_kline('600206.SH', start_date='20260101', end_date='20260731')
    print(f'600206数据量: {len(stock_data)}')
    return etf_data, stock_data, my_etfs


def compute_etf_state_full(df, etf_code, trade_date):
    """拷贝etf_breakout_strategy里的完整计算"""
    df_slice = df[df["trade_date"] <= trade_date].copy()
    if len(df_slice) < 40:
        return None
    C = df_slice["close"].values
    H = df_slice["high"].values

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
    momentum_40d = C[-1]/C[-41] -1 if len(C)>=41 else 0

    high_20_prev = rolling_max(H, 20)
    if np.isnan(high_20_prev[-1]):
        return None
    breakout = C[-1] > high_20_prev[-1]
    breakout_strength = (C[-1] - high_20_prev[-1]) / high_20_prev[-1] if breakout else 0

    is_strong = (trend_score > 0) and breakout

    etf_score = 0.3 * trend_score + 0.4 * momentum_40d + 0.2 * momentum + 0.1 * breakout_strength

    return {
        'etf_code': etf_code,
        'trade_date': trade_date,
        'trend_score': trend_score,
        'momentum': momentum,
        'momentum_40d': momentum_40d,
        'breakout': breakout,
        'is_strong': is_strong,
        'etf_score': etf_score
    }


def check_stock_breakout_full(df, ts_code, trade_date):
    """拷贝个股突破检查的完整逻辑"""
    board = get_board(ts_code)
    cfg = BoardConfig(
        donchian_period=10 if board == 'MB' else 12,
        vol_ratio_min=0.9 if board == 'MB' else 1.0,
        atr_stop_mult=1.5 if board == 'MB' else 2.0,
        atr_trail_mult=3.5 if board == 'MB' else 3.0,
        rsi_max=80 if board == 'MB' else 75 if board == 'CYB' else 80,
        ema_period=50,
        max_hold=30,
        etf_weak_days=4 if board == 'MB' else 3
    )
    df_slice = df[df["trade_date"] <= trade_date].copy()
    if len(df_slice) < 50:
        return None
    last = df_slice.iloc[-1]
    C = df_slice["close"].values
    H = df_slice["high"].values
    VOL = df_slice["vol"].values

    # 指标函数
    def ema(arr, n):
        return pd.Series(arr).ewm(span=n, adjust=False).mean().values

    def rsi(arr, n=14):
        s = pd.Series(arr)
        delta = s.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1/n, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/n, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return (100 - 100 / (1 + rs)).fillna(50).values

    def rolling_max(arr, n):
        return pd.Series(arr).rolling(n).max().shift(1).values

    def rolling_mean(arr, n):
        return pd.Series(arr).rolling(n).mean().values

    # 1. Donchian突破
    high_prev = rolling_max(H, cfg.donchian_period)
    donchian_ok = C[-1] > high_prev[-1]
    print(f'    1. Donchian {cfg.donchian_period}日: 前高={high_prev[-1]:.2f}, 收盘={C[-1]:.2f}, ok={donchian_ok}')

    # 2. 成交量
    vol_ma20 = rolling_mean(VOL, 20)
    vol_ok = VOL[-1] >= vol_ma20[-1] * cfg.vol_ratio_min
    print(f'    2. 成交量: 当日={VOL[-1]:.1f}, 20日均={vol_ma20[-1]:.1f}, 比值={VOL[-1]/vol_ma20[-1]:.2f}, ok={vol_ok}')

    # 3. EMA趋势
    ema_trend = ema(C, cfg.ema_period)
    ema_ok = C[-1] > ema_trend[-1]
    print(f'    3. EMA趋势: EMA{cfg.ema_period}={ema_trend[-1]:.2f}, ok={ema_ok}')

    # 4. RSI
    rsi_val = rsi(C, 14)
    rsi_ok = rsi_val[-1] < cfg.rsi_max
    print(f'    4. RSI: {rsi_val[-1]:.2f}, 上限={cfg.rsi_max}, ok={rsi_ok}')

    # 5. 短期动量
    mom_ok = len(C)>=4 and ((C[-1]/C[-4]-1 >=0) or (C[-1]/C[-6]-1 >=0))
    print(f'    5. 短期动量: ok={mom_ok}')

    # 6. EMA10
    ema10_val = ema(C, 10)
    ema10_ok = C[-1] > ema10_val[-1]
    print(f'    6. EMA10: ok={ema10_ok}')

    all_ok = donchian_ok and vol_ok and ema_ok and rsi_ok and mom_ok and ema10_ok
    print(f'    全部条件: {all_ok}')
    return all_ok


def main():
    etf_data, stock_data, my_etfs = load_all_data()
    # 重新加载etf_constituents
    import json
    cache_file = r"D:\mystock\cache_daily\etf_constituents_all.json"
    with open(cache_file, 'r', encoding='utf-8') as f:
        etf_constituents = json.load(f)
    dates = ['20260610', '20260611', '20260612', '20260615', '20260616', '20260617', '20260618']
    for d in dates:
        print(f'\n===== 检查日期: {d} =====')

        # 先检查ETF
        strong_etfs = []
        for etf in my_etfs:
            if etf in etf_data:
                state = compute_etf_state_full(etf_data[etf], etf, d)
                if state:
                    print(f'ETF {etf}状态: is_strong={state["is_strong"]}, trend={state["trend_score"]:.4f}, breakout={state["breakout"]}')
                    if state["is_strong"]:
                        strong_etfs.append(state)

        if len(strong_etfs) >0:
            print(f'有{len(strong_etfs)}只强势ETF')

            # 按得分排序
            strong_etfs.sort(key=lambda x: -x["etf_score"])
            top3 = strong_etfs[:3]
            print(f'选择前3名ETF: {[s["etf_code"] for s in top3]}')

            # 检查个股
            for etf_state in top3:
                if '600206.SH' in etf_constituents[etf_state['etf_code']]:
                    print(f'600206是ETF{etf_state["etf_code"]}的成份股')
                    check_stock_breakout_full(stock_data, '600206.SH', d)

        else:
            print('无强势ETF')


if __name__ == '__main__':
    main()
