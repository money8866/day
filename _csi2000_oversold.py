# -*- coding: utf-8 -*-
"""中证2000超跌情况分析"""
import sys
sys.path.insert(0, r'D:\mystock')
import tushare as ts
import pandas as pd
from datetime import datetime, timedelta

# Tushare token
pro = ts.pro_api('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')

# 获取中证2000指数K线（最近250个交易日）
print("获取中证2000指数数据...")
df = pro.index_daily(ts_code='932000.CSI', start_date='20250101', end_date='20260721')

if df.empty or len(df) < 50:
    print("数据不足")
    sys.exit(1)

# 按日期排序
df = df.sort_values('trade_date').reset_index(drop=True)
print(f"数据范围: {df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]}, 共{len(df)}条")

# 计算技术指标
df['ma5'] = df['close'].rolling(5).mean()
df['ma10'] = df['close'].rolling(10).mean()
df['ma20'] = df['close'].rolling(20).mean()
df['ma60'] = df['close'].rolling(60).mean()

# RSI
def calc_rsi(close, period=14):
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

df['rsi6'] = calc_rsi(df['close'], 6)
df['rsi14'] = calc_rsi(df['close'], 14)
df['rsi24'] = calc_rsi(df['close'], 24)

# 获取最近数据
latest = df.iloc[-1]
prev = df.iloc[-2]

# 计算涨跌幅
latest_close = latest['close']
prev_close = prev['close']
today_change = (latest_close - prev_close) / prev_close * 100

# 计算20日/60日涨跌幅
ma20_prev = df.iloc[-21]['close'] if len(df) > 20 else df.iloc[0]['close']
ma60_prev = df.iloc[-61]['close'] if len(df) > 60 else df.iloc[0]['close']
change_20d = (latest_close - ma20_prev) / ma20_prev * 100
change_60d = (latest_close - ma60_prev) / ma60_prev * 100

# 计算历史高点回撤
high_60d = df['close'].iloc[-60:].max() if len(df) >= 60 else df['close'].max()
high_120d = df['close'].iloc[-120:].max() if len(df) >= 120 else df['close'].max()
high_all = df['close'].max()

drawdown_60d = (high_60d - latest_close) / high_60d * 100
drawdown_120d = (high_120d - latest_close) / high_120d * 100
drawdown_all = (high_all - latest_close) / high_all * 100

# 均线状态
ma_status = []
if latest_close < latest['ma5']: ma_status.append("MA5下方")
if latest_close < latest['ma10']: ma_status.append("MA10下方")
if latest_close < latest['ma20']: ma_status.append("MA20下方")
if latest_close < latest['ma60']: ma_status.append("MA60下方")

# RSI超卖判断
rsi_status = []
if latest['rsi6'] < 20: rsi_status.append(f"RSI6={latest['rsi6']:.1f} 极度超卖")
elif latest['rsi6'] < 30: rsi_status.append(f"RSI6={latest['rsi6']:.1f} 超卖")
if latest['rsi14'] < 30: rsi_status.append(f"RSI14={latest['rsi14']:.1f} 超卖")
if latest['rsi24'] < 30: rsi_status.append(f"RSI24={latest['rsi24']:.1f} 超卖")

print("\n" + "="*60)
print("中证2000（932000.CSI）超跌分析")
print("="*60)
print(f"日期: {latest['trade_date']}")
print(f"收盘: {latest_close:.2f}")
print(f"今日涨跌: {today_change:+.2f}%")
print()
print(f"【均线状态】{' | '.join(ma_status) if ma_status else '均线上方'}")
print(f"  MA5={latest['ma5']:.2f}  MA10={latest['ma10']:.2f}  MA20={latest['ma20']:.2f}  MA60={latest['ma60']:.2f}")
print()
print(f"【RSI指标】")
print(f"  RSI6={latest['rsi6']:.1f}  RSI14={latest['rsi14']:.1f}  RSI24={latest['rsi24']:.1f}")
if rsi_status:
    print(f"  ⚠️ {' | '.join(rsi_status)}")
else:
    print(f"  ✅ 无超卖信号")
print()
print(f"【涨跌幅】")
print(f"  近20日: {change_20d:+.2f}%")
print(f"  近60日: {change_60d:+.2f}%")
print()
print(f"【回撤幅度】")
print(f"  较60日高点回撤: {drawdown_60d:.2f}%")
print(f"  较120日高点回撤: {drawdown_120d:.2f}%")
print(f"  较历史高点回撤: {drawdown_all:.2f}%")
print()

# 超跌判断
oversold_signals = 0
if latest['rsi6'] < 20: oversold_signals += 2
elif latest['rsi6'] < 30: oversold_signals += 1
if latest['rsi14'] < 30: oversold_signals += 1
if drawdown_60d > 15: oversold_signals += 1
if drawdown_120d > 20: oversold_signals += 1

print(f"【超跌判断】")
if oversold_signals >= 4:
    print(f"  🔴 严重超跌（信号数{oversold_signals}），反弹概率较高")
elif oversold_signals >= 2:
    print(f"  🟡 轻度超跌（信号数{oversold_signals}），观察反弹机会")
else:
    print(f"  🟢 未超跌（信号数{oversold_signals}）")

print("="*60)

# 近5日数据
print("\n近5日K线:")
for i in range(-5, 0):
    row = df.iloc[i]
    print(f"  {row['trade_date']}: {row['close']:.2f} ({(row['close']-df.iloc[i-1]['close'])/df.iloc[i-1]['close']*100:+.2f}%)  RSI6={row['rsi6']:.1f}")
