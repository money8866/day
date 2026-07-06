# -*- coding: utf-8 -*-
"""全量数据胜率分析 - 寻找60%+条件"""
import pandas as pd
import numpy as np

df = pd.read_csv('trend_entry_trades_mv100_full.csv', encoding='utf-8-sig')

print('全量119,009笔 - 寻找60%+胜率条件')
print('=' * 70)

# 1. 低动量分析(全量)
print('\n1. 低动量分析:')
for mom_max in [5, 8, 10, 12, 15]:
    subset = df[(df['momentum'] >= -5) & (df['momentum'] < mom_max)]
    if len(subset) > 100:
        wr = subset['win'].mean() * 100
        ar = subset['hold_return'].mean()
        print('  动量[-5%%-%d%%]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (mom_max, len(subset), wr, ar))

# 2. 三重组合搜索
print('\n2. 三重组合搜索:')
best_combos = []

for rsi_min, rsi_max in [(40, 50), (45, 55), (50, 60), (55, 65), (60, 70), (65, 75)]:
    for vol_min, vol_max in [(1.0, 1.5), (1.5, 2.0), (2.0, 2.5)]:
        for mom_min, mom_max in [(-5, 5), (-5, 10), (0, 10), (0, 15), (5, 15)]:
            subset = df[(df['rsi6'] >= rsi_min) & (df['rsi6'] < rsi_max) &
                       (df['vol_ratio'] >= vol_min) & (df['vol_ratio'] < vol_max) &
                       (df['momentum'] >= mom_min) & (df['momentum'] < mom_max)]
            if len(subset) >= 500:
                wr = subset['win'].mean() * 100
                ar = subset['hold_return'].mean()
                best_combos.append({
                    'rsi': '[%d-%d]' % (rsi_min, rsi_max),
                    'vol': '[%.1f-%.1f]' % (vol_min, vol_max),
                    'mom': '[%d-%d]' % (mom_min, mom_max),
                    'count': len(subset),
                    'win_rate': wr,
                    'avg_return': ar
                })

# 按胜率排序
best_combos.sort(key=lambda x: x['win_rate'], reverse=True)

print('\n胜率最高的组合(样本>=500):')
for i, c in enumerate(best_combos[:15]):
    print('  %d. RSI%s + 量比%s + 动量%s%%: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (
        i+1, c['rsi'], c['vol'], c['mom'], c['count'], c['win_rate'], c['avg_return']))

# 3. 精确高胜率条件
print('\n' + '=' * 70)
print('精确高胜率条件验证')
print('=' * 70)

# 条件1: RSI[55-65] + 量比[1.5-2.0] + 动量[0-15]
subset = df[(df['rsi6'] >= 55) & (df['rsi6'] < 65) &
           (df['vol_ratio'] >= 1.5) & (df['vol_ratio'] < 2.0) &
           (df['momentum'] >= 0) & (df['momentum'] < 15)]
if len(subset) > 0:
    wr = subset['win'].mean() * 100
    ar = subset['hold_return'].mean()
    print('RSI[55-65] + 量比[1.5-2.0] + 动量[0-15%%]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (len(subset), wr, ar))

# 条件2: RSI[50-60] + 量比[1.0-1.5] + 动量[-5-10]
subset = df[(df['rsi6'] >= 50) & (df['rsi6'] < 60) &
           (df['vol_ratio'] >= 1.0) & (df['vol_ratio'] < 1.5) &
           (df['momentum'] >= -5) & (df['momentum'] < 10)]
if len(subset) > 0:
    wr = subset['win'].mean() * 100
    ar = subset['hold_return'].mean()
    print('RSI[50-60] + 量比[1.0-1.5] + 动量[-5-10%%]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (len(subset), wr, ar))

# 条件3: RSI[45-55] + 量比[1.0-1.5] + 动量[0-12]
subset = df[(df['rsi6'] >= 45) & (df['rsi6'] < 55) &
           (df['vol_ratio'] >= 1.0) & (df['vol_ratio'] < 1.5) &
           (df['momentum'] >= 0) & (df['momentum'] < 12)]
if len(subset) > 0:
    wr = subset['win'].mean() * 100
    ar = subset['hold_return'].mean()
    print('RSI[45-55] + 量比[1.0-1.5] + 动量[0-12%%]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (len(subset), wr, ar))
