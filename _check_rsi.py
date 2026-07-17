# -*- coding: utf-8 -*-
import tushare as ts
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

pro = ts.pro_api('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')

indices = [
    ('000001.SH', '上证指数'),
    ('399001.SZ', '深证成指'),
    ('399006.SZ', '创业板指'),
    ('000300.SH', '沪深300'),
    ('000688.SH', '科创50'),
    ('000016.SH', '上证50'),
    ('000905.SH', '中证500'),
    ('000852.SH', '中证1000'),
]

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes, prepend=closes[0])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = pd.Series(gains).rolling(period).mean().iloc[-1]
    avg_loss = pd.Series(losses).rolling(period).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

print('=== 指数超跌诊断（2026-07-17 收盘）===\n')
print(f"{'指数':<10} {'最新收盘':>10} {'5日涨跌':>8} {'RSI6':>6} {'RSI14':>6} {'RSI20':>6} {'RSI评价':>8}")
print('-' * 65)

for code, name in indices:
    try:
        df = pro.index_daily(ts_code=code, end_date='20260718', limit=30)
        if df is None or df.empty:
            print(f"{name:<10} 无数据")
            continue
        df = df.sort_values('trade_date')
        closes = df['close'].values

        pct5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0
        pct10 = (closes[-1] / closes[-11] - 1) * 100 if len(closes) >= 11 else 0
        pct20 = (closes[-1] / closes[-21] - 1) * 100 if len(closes) >= 21 else 0

        rsi6 = calc_rsi(closes, 6)
        rsi14 = calc_rsi(closes, 14)
        rsi20 = calc_rsi(closes, 20)

        if rsi14 < 20:
            tag = '极度超卖'
        elif rsi14 < 30:
            tag = '超卖'
        elif rsi14 < 40:
            tag = '偏弱'
        elif rsi14 < 60:
            tag = '中性'
        else:
            tag = '偏强'

        pct5_str = f"{pct5:+.1f}%"
        print(f"{name:<10} {closes[-1]:>10.2f} {pct5_str:>8} {rsi6:>6.1f} {rsi14:>6.1f} {rsi20:>6.1f} {tag:>8}")
    except Exception as e:
        print(f"{name:<10} 错误: {str(e)[:40]}")

print()
print('=== 综合判断 ===')
print(f"诊断时间: 2026-07-17 收盘")
print()
