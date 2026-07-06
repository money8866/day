# -*- coding: utf-8 -*-
"""
检查600206.SH为什么在20260610-11没被选中
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, 'd:\\mystock\\tdx_backtest')
from data_loader import load_kline
import warnings
warnings.filterwarnings('ignore')


# 复制所需的指标函数
def ema(arr: np.ndarray, n: int) -> np.ndarray:
    s = pd.Series(arr)
    return s.ewm(span=n, adjust=False).mean().values


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> np.ndarray:
    h = pd.Series(high)
    l = pd.Series(low)
    c = pd.Series(close)
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean().values


def rsi(close: np.ndarray, n: int = 14) -> np.ndarray:
    s = pd.Series(close)
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/n, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50).values


def rolling_max(arr: np.ndarray, n: int) -> np.ndarray:
    s = pd.Series(arr)
    return s.rolling(n).max().shift(1).values


def rolling_mean(arr: np.ndarray, n: int) -> np.ndarray:
    s = pd.Series(arr)
    return s.rolling(n).mean().values


# 加载数据
print('加载600206.SH的数据...')
df_full = load_kline('600206.SH', start_date='20260401', end_date='20260630')
print(f'数据行数: {len(df_full)}, 列: {df_full.columns}')
print(f'最近30行:')
print(df_full[['trade_date', 'open', 'high', 'low', 'close', 'vol', 'amount']].tail(30))

# 首先找到600206属于哪个ETF！
sys.path.insert(0, 'd:\\mystock\\solo')
try:
    from etf_mainline_strategy_tushare import get_etf_constituents
    # 从缓存读ETF成分股
    import json
    cache_file = r'd:\mystock\cache_daily\etf_constituents_all.json'
    with open(cache_file, 'r', encoding='utf-8') as f:
        etf_constituents = json.load(f)
    print(f'\n检查600206.SH属于哪些ETF...')
    found = False
    for etf_code, constituents in etf_constituents.items():
        # 先把成分股列表转成字符串
        cons_str = [str(c) for c in constituents]
        if '600206.SH' in cons_str or '600206' in cons_str:
            print(f'找到！属于ETF: {etf_code}')
            found = True
    if not found:
        print('没找到！')
except Exception as e:
    print(f'读取ETF成分股失败: {e}')

# 然后逐个检查600206在20260601-20260620每个交易日的突破信号情况
print(f'\n检查600206在20260601-20260620每个交易日的情况...')
dates_to_check = ['20260601', '20260602', '20260603', '20260604', '20260605',
                  '20260608', '20260609', '20260610', '20260611', '20260612',
                  '20260615', '20260616', '20260617', '20260618', '20260619']

for date_str in dates_to_check:
    # 筛选到当前日期的数据
    df = df_full[df_full['trade_date'] <= date_str].copy()
    if len(df) < 50:
        continue
    C = df['close'].values
    H = df['high'].values
    L = df['low'].values
    VOL = df['vol'].values
    # 主板参数
    donchian_period = 10
    vol_ratio_min = 0.9
    ema_period = 50
    rsi_max = 75
    # 1. Donchian突破
    high_n_prev = rolling_max(H, donchian_period)
    donchian_ok = C[-1] > high_n_prev[-1]
    # 2. 量能
    vol_ma20 = rolling_mean(VOL, 20)
    vol_ok = VOL[-1] >= vol_ma20[-1] * vol_ratio_min if not np.isnan(vol_ma20[-1]) else False
    # 3. EMA趋势
    ema_trend = ema(C, ema_period)
    ema_ok = C[-1] > ema_trend[-1]
    # 4. RSI
    rsi_val = rsi(C, 14)
    rsi_ok = rsi_val[-1] < rsi_max
    # 5. 连续阴跌过滤
    drop_ok = len(C) >= 21 and (C[-1] / C[-21] - 1 >= -0.1)
    # 6. 短期动量
    mom_ok = len(C) >= 4 and ((C[-1] / C[-4] -1 >= 0) or (C[-1]/C[-6]-1 >= 0))
    # 7. EMA10
    ema10 = ema(C, 10)
    ema10_ok = C[-1] > ema10[-1]
    # 综合
    all_ok = donchian_ok and vol_ok and ema_ok and rsi_ok and drop_ok and mom_ok and ema10_ok

    print(f'\n{date_str}:')
    print(f'  收盘: {C[-1]}, {donchian_period}日最高(不含当日): {high_n_prev[-1]}, 突破: {donchian_ok}')
    print(f'  量能: {VOL[-1]}, 20日均量: {vol_ma20[-1]}, 量比: {VOL[-1]/vol_ma20[-1] if vol_ma20[-1]>0 else 0}, 量能ok: {vol_ok}')
    print(f'  EMA{ema_period}: {ema_trend[-1]}, EMA10: {ema10[-1]}')
    print(f'  RSI: {rsi_val[-1]}')
    print(f'  短期动量: 3日{(C[-1]/C[-4]-1)*100:.2f}%, 5日{(C[-1]/C[-6]-1)*100:.2f}%, 动量ok: {mom_ok}')
    print(f'  全部条件通过: {all_ok}')
