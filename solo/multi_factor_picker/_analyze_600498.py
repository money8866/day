# -*- coding: utf-8 -*-
"""烽火通信20260611突破震荡分析"""
import os, sys
sys.path.insert(0, r'D:\mystock')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'

import tushare as ts
import pandas as pd
import numpy as np
ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

code = '600498.SH'
df = pro.stk_factor_pro(ts_code=code, start_date='20250101', end_date='20260624')
if df is None or len(df) == 0:
    print('无数据')
    sys.exit(1)

df = df.sort_values('trade_date').reset_index(drop=True)

# 找到20260611
target_idx = df[df['trade_date'] == '20260611'].index
if len(target_idx) == 0:
    print('未找到20260611')
    sys.exit(1)

idx = target_idx[0]
print('=' * 70)
print('烽火通信 600498.SH - 20260611突破震荡分析')
print('=' * 70)

print(f'\n突破日数据:')
print(f'  收盘价: {df.iloc[idx]["close"]:.2f}')
print(f'  涨幅: {df.iloc[idx]["pct_chg"]:.1f}%')
print(f'  量比: {df.iloc[idx].get("volume_ratio", 0):.2f}')
print(f'  RSI-6: {df.iloc[idx].get("rsi_qfq_6", 0):.1f}')
print(f'  MACD: DIF={df.iloc[idx].get("macd_dif_qfq", 0):.2f} DEA={df.iloc[idx].get("macd_dea_qfq", 0):.2f}')
print(f'  ADX: {df.iloc[idx].get("dmi_adx_qfq", 0):.1f}')

# 震荡区间分析
print('\n=== 震荡区间分析（前60天）===')
window = df.iloc[max(0, idx-60):idx]
if len(window) > 0:
    high = window['close'].max()
    low = window['close'].min()
    mid = (high + low) / 2
    amplitude = (high - low) / low * 100

    high_date = window[window['close'] == high]['trade_date'].values[0]
    low_date = window[window['close'] == low]['trade_date'].values[0]

    print(f'震荡高点: {high:.2f} ({high_date})')
    print(f'震荡低点: {low:.2f} ({low_date})')
    print(f'震荡幅度: {amplitude:.1f}%')
    print(f'中位线: {mid:.2f}')
    print(f'突破幅度: {(df.iloc[idx]["close"] - high) / high * 100:.2f}%')

    # 震荡期间涨跌统计
    up_days = (window['pct_chg'] > 0).sum()
    down_days = (window['pct_chg'] < 0).sum()
    print(f'震荡天数: {len(window)}天 (涨{up_days} 跌{down_days})')

# 突破前形态
print('\n=== 突破前10天形态 ===')
for i in range(max(0, idx-10), idx):
    r = df.iloc[i]
    vol_ratio = r.get('volume_ratio', 0)
    rsi = r.get('rsi_qfq_6', 0)
    print(f'{r["trade_date"]} 收盘{r["close"]:.2f} {r["pct_chg"]:+5.1f}% 量比{vol_ratio:.2f} RSI{rsi:.0f}')

# 突破后走势
print('\n=== 突破后走势 ===')
for i in range(idx, min(idx+10, len(df))):
    r = df.iloc[i]
    gain = (r['close'] - df.iloc[idx]['close']) / df.iloc[idx]['close'] * 100 if i > idx else 0
    vol_ratio = r.get('volume_ratio', 0)
    rsi = r.get('rsi_qfq_6', 0)
    marker = '←突破' if i == idx else ''
    print(f'{r["trade_date"]} 收盘{r["close"]:.2f} {r["pct_chg"]:+5.1f}% 累计{gain:+5.1f}% 量比{vol_ratio:.2f} RSI{rsi:.0f} {marker}')

# 评分计算
print('\n=== 多指标共振评分 ===')
row = df.iloc[idx]
score = 0
details = []

rsi = float(row.get('rsi_qfq_6', 50))
if rsi < 40: score += 2; details.append(f'RSI={rsi:.0f}<40')
elif rsi < 50: score += 1; details.append(f'RSI={rsi:.0f}<50')

vol_ratio = float(row.get('volume_ratio', 1.0))
if vol_ratio > 1.5: score += 2; details.append(f'量比={vol_ratio:.2f}>1.5')
elif vol_ratio > 1.2: score += 1; details.append(f'量比={vol_ratio:.2f}>1.2')

macd_dif = float(row.get('macd_dif_qfq', 0))
macd_dea = float(row.get('macd_dea_qfq', 0))
if macd_dif > macd_dea: score += 2; details.append('MACD金叉')

adx = float(row.get('dmi_adx_qfq', 0))
if adx > 25: score += 2; details.append(f'ADX={adx:.0f}>25')

close = float(row['close'])
ma20 = float(row.get('ma_qfq_20', 0))
ma60 = float(row.get('ma_qfq_60', 0))
if close > ma20: score += 1; details.append('MA20上方')
if close > ma60: score += 1; details.append('MA60上方')

if rsi > 70: score -= 3; details.append(f'RSI>70超买(-3)')

print(f'评分: {score}分')
print(f'详情: {"; ".join(details)}')
