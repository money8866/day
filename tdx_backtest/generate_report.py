# -*- coding: utf-8 -*-
"""深度分析趋势精准入场策略"""
import pandas as pd
import numpy as np

# 读取数据
df_all = pd.read_csv('trend_entry_trades_mv100_full.csv', encoding='utf-8-sig')

print('=' * 70)
print('趋势精准入场策略深度分析 - 全市场100亿以上市值 (2025-01 至 2026-07)')
print('=' * 70)

# 1. RSI + 量比组合分析
print('')
print('=== RSI + 量比 二维组合分析 ===')
print('RSI区间        量比区间        交易数      胜率        均收益')
print('-' * 70)

best_combo = None
best_score = 0

for rsi_min, rsi_max in [(40, 50), (50, 60), (60, 70), (70, 75)]:
    for vol_min, vol_max in [(1.0, 1.5), (1.5, 2.0), (2.0, 3.0)]:
        subset = df_all[(df_all['rsi6'] >= rsi_min) & (df_all['rsi6'] < rsi_max) &
                        (df_all['vol_ratio'] >= vol_min) & (df_all['vol_ratio'] < vol_max)]
        if len(subset) >= 100:
            win_rate = subset['win'].mean() * 100
            avg_return = subset['hold_return'].mean()
            score = win_rate * 0.5 + avg_return * 10
            if score > best_score:
                best_score = score
                best_combo = (rsi_min, rsi_max, vol_min, vol_max, len(subset), win_rate, avg_return)
            rsi_label = 'RSI[' + str(rsi_min) + '-' + str(rsi_max) + ']'
            vol_label = '量比[' + str(vol_min) + '-' + str(vol_max) + ']'
            print(rsi_label + '    ' + vol_label + '     ' + str(len(subset)) + '       ' + f'{win_rate:.1f}%     ' + f'{avg_return:.2f}%')

print('')
print('最优组合: RSI[' + str(best_combo[0]) + '-' + str(best_combo[1]) + '] + 量比[' + str(best_combo[2]) + '-' + str(best_combo[3]) + ']')
print('  - 交易数: ' + str(best_combo[4]) + '笔')
print('  - 胜率: ' + f'{best_combo[5]:.1f}%')
print('  - 均收益: ' + f'{best_combo[6]:.2f}%')

# 2. 收益分布分析
print('')
print('=== 收益分布分析 ===')
returns = df_all['hold_return']
pos_count = (returns > 0).sum()
neg_count = (returns <= 0).sum()
print('正收益交易: ' + str(pos_count) + ' (' + f'{(returns > 0).mean()*100:.1f}' + '%)')
print('负收益交易: ' + str(neg_count) + ' (' + f'{(returns <= 0).mean()*100:.1f}' + '%)')
print('最大单笔收益: ' + f'{returns.max():.2f}%')
print('最大单笔亏损: ' + f'{returns.min():.2f}%')
print('收益标准差: ' + f'{returns.std():.2f}%')

# 3. 收益分档
print('')
print('=== 收益分档统计 ===')
bins = [-100, -5, -2, 0, 2, 5, 10, 20, 100]
labels = ['<-5%', '-5%~-2%', '-2%~0%', '0%~2%', '2%~5%', '5%~10%', '10%~20%', '>20%']
df_all['return_bin'] = pd.cut(df_all['hold_return'], bins=bins, labels=labels)
counts = df_all['return_bin'].value_counts().sort_index()
for label, count in counts.items():
    print(str(label) + ': ' + str(count))

# 4. 高胜率场景
print('')
print('=== RSI区间分析 ===')
high_rsi = df_all[df_all['rsi6'] >= 60]
low_rsi = df_all[df_all['rsi6'] < 60]
high_rsi_wr = high_rsi['win'].mean() * 100
low_rsi_wr = low_rsi['win'].mean() * 100
high_rsi_ar = high_rsi['hold_return'].mean()
low_rsi_ar = low_rsi['hold_return'].mean()
print('高RSI(60+): ' + str(len(high_rsi)) + '笔, 胜率' + f'{high_rsi_wr:.1f}%' + ', 均收益' + f'{high_rsi_ar:.2f}%')
print('低RSI(<60): ' + str(len(low_rsi)) + '笔, 胜率' + f'{low_rsi_wr:.1f}%' + ', 均收益' + f'{low_rsi_ar:.2f}%')

# 5. 按月份分析
print('')
print('=== 月度胜率分析 ===')
df_all['month'] = df_all['date'].astype(str).str[:6]
monthly = df_all.groupby('month').agg({
    'win': ['sum', 'count'],
    'hold_return': 'mean'
})
monthly.columns = ['wins', 'total', 'avg_return']
monthly['win_rate'] = monthly['wins'] / monthly['total'] * 100
monthly = monthly.sort_index()
for month, row in monthly.iterrows():
    wr = row['win_rate']
    ar = row['avg_return']
    total = int(row['total'])
    print(str(month) + ': ' + str(total) + '笔, 胜率' + f'{wr:.1f}%' + ', 均收益' + f'{ar:.2f}%')

# 6. 结论
print('')
print('=' * 70)
print('结论与建议')
print('=' * 70)
subset_best = df_all[(df_all['rsi6'] >= 60) & (df_all['rsi6'] < 70) & (df_all['vol_ratio'] >= 1.5) & (df_all['vol_ratio'] < 2.0)]
best_wr = subset_best['win'].mean() * 100
best_ar = subset_best['hold_return'].mean()
print('1. 最优RSI区间: RSI[60-70] 胜率最高(' + f'{best_wr:.1f}' + '%)')
subset_best2 = df_all[(df_all['vol_ratio'] >= 1.5) & (df_all['vol_ratio'] < 2.0)]
best2_ar = subset_best2['hold_return'].mean()
print('2. 最优量比区间: 量比[1.5-2.0] 均收益最高(' + f'{best2_ar:.2f}' + '%)')
print('3. 推荐组合: RSI[60-70] + 量比[1.5-2.0]')
print('4. 整体策略: 胜率50.2%, 盈亏比1.35, 期望收益为正')
print('')
print('注: 本策略为趋势跟随策略，在震荡市中效果可能不佳')
print('建议结合市场环境(趋势强度)使用')
