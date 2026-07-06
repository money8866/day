# -*- coding: utf-8 -*-
"""分析最优条件"""
import pandas as pd

df = pd.read_csv('trend_entry_trades_mv100_full.csv', encoding='utf-8-sig')

print('=' * 70)
print('全量119,009笔交易 - 胜率分析')
print('=' * 70)

# 1. RSI区间
print('\n1. RSI区间:')
for rsi_min, rsi_max in [(40,50), (50,55), (55,60), (60,65), (65,70), (70,75)]:
    subset = df[(df['rsi6'] >= rsi_min) & (df['rsi6'] < rsi_max)]
    if len(subset) > 100:
        wr = subset['win'].mean() * 100
        ar = subset['hold_return'].mean()
        print('  RSI[%d-%d]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (rsi_min, rsi_max, len(subset), wr, ar))

# 2. 量比区间
print('\n2. 量比区间:')
for vol_min, vol_max in [(1.0,1.5), (1.5,2.0), (2.0,2.5), (2.5,3.0), (3.0,4.0)]:
    subset = df[(df['vol_ratio'] >= vol_min) & (df['vol_ratio'] < vol_max)]
    if len(subset) > 100:
        wr = subset['win'].mean() * 100
        ar = subset['hold_return'].mean()
        print('  量比[%.1f-%.1f]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (vol_min, vol_max, len(subset), wr, ar))

# 3. 持有收益区间
print('\n3. 持有收益区间:')
for ret_min, ret_max in [(-10,-5), (-5,-2), (-2,0), (0,2), (2,5), (5,10), (10,20)]:
    subset = df[(df['hold_return'] >= ret_min) & (df['hold_return'] < ret_max)]
    if len(subset) > 100:
        wr = subset['win'].mean() * 100
        ar = subset['hold_return'].mean()
        print('  收益[%d%%-%d%%]: %d笔, 胜率%.1f%%' % (ret_min, ret_max, len(subset), wr))

# 4. 组合条件寻找60%+胜率
print('\n' + '=' * 70)
print('寻找60%+胜率的组合条件')
print('=' * 70)

best_conditions = []

# 遍历RSI和量比组合
for rsi_min in [40, 45, 50, 55, 60]:
    for rsi_max in [rsi_min + 10, rsi_min + 15]:
        if rsi_max > 80:
            continue
        for vol_min in [1.0, 1.2, 1.5]:
            for vol_max in [2.0, 2.5, 3.0]:
                if vol_max <= vol_min:
                    continue
                subset = df[(df['rsi6'] >= rsi_min) & (df['rsi6'] < rsi_max) &
                           (df['vol_ratio'] >= vol_min) & (df['vol_ratio'] < vol_max)]
                if len(subset) >= 1000:
                    wr = subset['win'].mean() * 100
                    ar = subset['hold_return'].mean()
                    if wr >= 52:
                        best_conditions.append({
                            'rsi': '[%d-%d]' % (rsi_min, rsi_max),
                            'vol': '[%.1f-%.1f]' % (vol_min, vol_max),
                            'count': len(subset),
                            'win_rate': wr,
                            'avg_return': ar
                        })

# 排序
best_conditions.sort(key=lambda x: x['win_rate'], reverse=True)

print('\n胜率>=52%的组合(前10):')
for i, c in enumerate(best_conditions[:10]):
    print('  %d. RSI%s + 量比%s: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (
        i+1, c['rsi'], c['vol'], c['count'], c['win_rate'], c['avg_return']))

# 5. 严格条件筛选60%+
print('\n' + '=' * 70)
print('严格条件60%+胜率')
print('=' * 70)

# 高RSI + 低量比
subset = df[(df['rsi6'] >= 65) & (df['rsi6'] < 75) & (df['vol_ratio'] >= 1.0) & (df['vol_ratio'] < 1.5)]
if len(subset) > 0:
    wr = subset['win'].mean() * 100
    ar = subset['hold_return'].mean()
    print('RSI[65-75] + 量比[1.0-1.5]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (len(subset), wr, ar))

# RSI[65-70] + 量比适中
subset = df[(df['rsi6'] >= 65) & (df['rsi6'] < 70) & (df['vol_ratio'] >= 1.2) & (df['vol_ratio'] < 2.0)]
if len(subset) > 0:
    wr = subset['win'].mean() * 100
    ar = subset['hold_return'].mean()
    print('RSI[65-70] + 量比[1.2-2.0]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (len(subset), wr, ar))

# RSI[60-70] + 量比[1.0-1.5]
subset = df[(df['rsi6'] >= 60) & (df['rsi6'] < 70) & (df['vol_ratio'] >= 1.0) & (df['vol_ratio'] < 1.5)]
if len(subset) > 0:
    wr = subset['win'].mean() * 100
    ar = subset['hold_return'].mean()
    print('RSI[60-70] + 量比[1.0-1.5]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (len(subset), wr, ar))

# RSI[65-70] + 量比[1.5-2.0]
subset = df[(df['rsi6'] >= 65) & (df['rsi6'] < 70) & (df['vol_ratio'] >= 1.5) & (df['vol_ratio'] < 2.0)]
if len(subset) > 0:
    wr = subset['win'].mean() * 100
    ar = subset['hold_return'].mean()
    print('RSI[65-70] + 量比[1.5-2.0]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (len(subset), wr, ar))
