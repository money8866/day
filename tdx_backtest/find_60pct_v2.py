# -*- coding: utf-8 -*-
"""全量数据胜率分析 - 使用hold_return作为动量代理"""
import pandas as pd
import numpy as np

df = pd.read_csv('trend_entry_trades_mv100_full.csv', encoding='utf-8-sig')

print('全量119,009笔 - 寻找60%+胜率条件')
print('=' * 70)

# 1. RSI + 量比组合
print('\n1. RSI + 量比组合:')
best_combos = []

for rsi_min, rsi_max in [(40, 50), (45, 55), (50, 60), (55, 65), (60, 70), (65, 75)]:
    for vol_min, vol_max in [(1.0, 1.5), (1.5, 2.0), (2.0, 2.5), (2.5, 3.0)]:
        subset = df[(df['rsi6'] >= rsi_min) & (df['rsi6'] < rsi_max) &
                   (df['vol_ratio'] >= vol_min) & (df['vol_ratio'] < vol_max)]
        if len(subset) >= 1000:
            wr = subset['win'].mean() * 100
            ar = subset['hold_return'].mean()
            best_combos.append({
                'rsi': '[%d-%d]' % (rsi_min, rsi_max),
                'vol': '[%.1f-%.1f]' % (vol_min, vol_max),
                'count': len(subset),
                'win_rate': wr,
                'avg_return': ar
            })

best_combos.sort(key=lambda x: x['win_rate'], reverse=True)

print('\n胜率最高的RSI+量比组合(样本>=1000):')
for i, c in enumerate(best_combos[:10]):
    print('  %d. RSI%s + 量比%s: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (
        i+1, c['rsi'], c['vol'], c['count'], c['win_rate'], c['avg_return']))

# 2. 精确验证最优条件
print('\n' + '=' * 70)
print('最优条件验证')
print('=' * 70)

# RSI[55-65] + 量比[1.5-2.0]
subset = df[(df['rsi6'] >= 55) & (df['rsi6'] < 65) &
           (df['vol_ratio'] >= 1.5) & (df['vol_ratio'] < 2.0)]
wr = subset['win'].mean() * 100
ar = subset['hold_return'].mean()
print('RSI[55-65] + 量比[1.5-2.0]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (len(subset), wr, ar))

# RSI[50-60] + 量比[1.5-2.0]
subset = df[(df['rsi6'] >= 50) & (df['rsi6'] < 60) &
           (df['vol_ratio'] >= 1.5) & (df['vol_ratio'] < 2.0)]
wr = subset['win'].mean() * 100
ar = subset['hold_return'].mean()
print('RSI[50-60] + 量比[1.5-2.0]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (len(subset), wr, ar))

# RSI[60-70] + 量比[1.5-2.0]
subset = df[(df['rsi6'] >= 60) & (df['rsi6'] < 70) &
           (df['vol_ratio'] >= 1.5) & (df['vol_ratio'] < 2.0)]
wr = subset['win'].mean() * 100
ar = subset['hold_return'].mean()
print('RSI[60-70] + 量比[1.5-2.0]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (len(subset), wr, ar))

# RSI[60-70] + 量比[1.0-1.5]
subset = df[(df['rsi6'] >= 60) & (df['rsi6'] < 70) &
           (df['vol_ratio'] >= 1.0) & (df['vol_ratio'] < 1.5)]
wr = subset['win'].mean() * 100
ar = subset['hold_return'].mean()
print('RSI[60-70] + 量比[1.0-1.5]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (len(subset), wr, ar))

# 3. 更窄RSI范围
print('\n3. 更窄RSI范围:')

# RSI[62-68]
subset = df[(df['rsi6'] >= 62) & (df['rsi6'] < 68) & (df['vol_ratio'] >= 1.5) & (df['vol_ratio'] < 2.0)]
wr = subset['win'].mean() * 100
ar = subset['hold_return'].mean()
print('RSI[62-68] + 量比[1.5-2.0]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (len(subset), wr, ar))

# RSI[58-64]
subset = df[(df['rsi6'] >= 58) & (df['rsi6'] < 64) & (df['vol_ratio'] >= 1.5) & (df['vol_ratio'] < 2.0)]
wr = subset['win'].mean() * 100
ar = subset['hold_return'].mean()
print('RSI[58-64] + 量比[1.5-2.0]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (len(subset), wr, ar))
