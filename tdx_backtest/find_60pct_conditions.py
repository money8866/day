# -*- coding: utf-8 -*-
"""寻找60%+胜率条件"""
import pandas as pd
import itertools

df = pd.read_csv('optimized_signals_v2.csv', encoding='utf-8-sig')

print('=' * 70)
print('寻找60%+胜率的精确条件')
print('=' * 70)

# 1. 低动量分析
print('\n1. 低动量信号分析:')
for mom_max in [10, 12, 15, 18, 20]:
    subset = df[(df['momentum'] >= 0) & (df['momentum'] < mom_max)]
    if len(subset) > 30:
        wr = subset['win'].mean() * 100
        ar = subset['hold_return'].mean()
        print('  动量[0-%d%%]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (mom_max, len(subset), wr, ar))

# 2. 低动量 + RSI
print('\n2. 低动量 + RSI组合:')
for mom_max in [10, 15, 20]:
    for rsi_min, rsi_max in [(50, 60), (55, 65), (60, 70)]:
        subset = df[(df['momentum'] >= 0) & (df['momentum'] < mom_max) & 
                   (df['rsi6'] >= rsi_min) & (df['rsi6'] < rsi_max)]
        if len(subset) > 30:
            wr = subset['win'].mean() * 100
            ar = subset['hold_return'].mean()
            print('  动量[0-%d%%] + RSI[%d-%d]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (
                mom_max, rsi_min, rsi_max, len(subset), wr, ar))

# 3. 量比分析
print('\n3. 量比分析:')
for vol_min, vol_max in [(1.0,1.3), (1.3,1.5), (1.5,1.8), (1.8,2.0)]:
    subset = df[(df['vol_ratio'] >= vol_min) & (df['vol_ratio'] < vol_max)]
    if len(subset) > 30:
        wr = subset['win'].mean() * 100
        ar = subset['hold_return'].mean()
        print('  量比[%.1f-%.1f]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (
            vol_min, vol_max, len(subset), wr, ar))

# 4. RSI低位分析
print('\n4. RSI低位分析:')
for rsi_min, rsi_max in [(40,45), (42,48), (45,50), (48,52)]:
    subset = df[(df['rsi6'] >= rsi_min) & (df['rsi6'] < rsi_max)]
    if len(subset) > 20:
        wr = subset['win'].mean() * 100
        ar = subset['hold_return'].mean()
        print('  RSI[%d-%d]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (
            rsi_min, rsi_max, len(subset), wr, ar))

# 5. 三重组合搜索
print('\n5. 三重组合搜索:')
best_combos = []

for rsi_min, rsi_max in [(50, 60), (55, 65), (45, 55)]:
    for vol_min, vol_max in [(1.0, 1.5), (1.5, 2.0)]:
        for mom_min, mom_max in [(0, 15), (0, 20), (5, 20)]:
            subset = df[(df['rsi6'] >= rsi_min) & (df['rsi6'] < rsi_max) &
                       (df['vol_ratio'] >= vol_min) & (df['vol_ratio'] < vol_max) &
                       (df['momentum'] >= mom_min) & (df['momentum'] < mom_max)]
            if len(subset) >= 50:
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

print('\n胜率最高的组合:')
for i, c in enumerate(best_combos[:10]):
    print('  %d. RSI%s + 量比%s + 动量%s%%: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (
        i+1, c['rsi'], c['vol'], c['mom'], c['count'], c['win_rate'], c['avg_return']))

# 6. 精确最优条件
print('\n' + '=' * 70)
print('最终推荐: 60%+胜率条件')
print('=' * 70)

# RSI[48-55] + 量比[1.0-1.5] + 动量[0-15]
subset = df[(df['rsi6'] >= 48) & (df['rsi6'] < 55) &
           (df['vol_ratio'] >= 1.0) & (df['vol_ratio'] < 1.5) &
           (df['momentum'] >= 0) & (df['momentum'] < 15)]
if len(subset) > 0:
    wr = subset['win'].mean() * 100
    ar = subset['hold_return'].mean()
    print('RSI[48-55] + 量比[1.0-1.5] + 动量[0-15%%]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (len(subset), wr, ar))

# RSI[50-60] + 量比[1.0-1.5] + 动量[0-12]
subset = df[(df['rsi6'] >= 50) & (df['rsi6'] < 60) &
           (df['vol_ratio'] >= 1.0) & (df['vol_ratio'] < 1.5) &
           (df['momentum'] >= 0) & (df['momentum'] < 12)]
if len(subset) > 0:
    wr = subset['win'].mean() * 100
    ar = subset['hold_return'].mean()
    print('RSI[50-60] + 量比[1.0-1.5] + 动量[0-12%%]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (len(subset), wr, ar))
