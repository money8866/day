# -*- coding: utf-8 -*-
"""汇总回测结果"""
import pandas as pd
import numpy as np

# 读取所有批次结果
all_trades = []
for i in range(1, 11):
    try:
        df = pd.read_csv(f'trend_entry_trades_batch_{i}.csv', encoding='utf-8-sig')
        all_trades.append(df)
        print(f'批次 {i}: {len(df)} 笔交易')
    except Exception as e:
        print(f'批次 {i} 文件不存在: {e}')

df_all = pd.concat(all_trades, ignore_index=True)

print()
print('=' * 60)
print('全市场100亿以上市值股票回测结果 (2025-01-01 至 2026-07-05)')
print('=' * 60)
print(f'总交易数: {len(df_all)}')
print(f'胜率: {df_all["win"].mean() * 100:.1f}%')
print(f'平均收益: {df_all["hold_return"].mean():.2f}%')
print(f'平均盈利: {df_all[df_all["win"]]["hold_return"].mean():.2f}%')
print(f'平均亏损: {df_all[~df_all["win"]]["hold_return"].mean():.2f}%')
pl_ratio = abs(df_all[df_all["win"]]["hold_return"].mean() / df_all[~df_all["win"]]["hold_return"].mean())
print(f'盈亏比: {pl_ratio:.2f}')

# 按评分分组
print()
print('=== 按RSI分组统计 ===')
for rsi_min, rsi_max, label in [(40, 50, 'RSI[40-50]'), (50, 60, 'RSI[50-60]'), (60, 70, 'RSI[60-70]'), (70, 75, 'RSI[70-75]')]:
    subset = df_all[(df_all['rsi6'] >= rsi_min) & (df_all['rsi6'] < rsi_max)]
    if len(subset) > 0:
        win_rate = subset['win'].mean() * 100
        avg_return = subset['hold_return'].mean()
        print(f'{label}: {len(subset)}笔, 胜率{win_rate:.1f}%, 均收益{avg_return:.2f}%')

# 按成交量分组
print()
print('=== 按量比分组统计 ===')
for vol_min, vol_max, label in [(1.0, 1.5, '量比[1.0-1.5]'), (1.5, 2.0, '量比[1.5-2.0]'), (2.0, 3.0, '量比[2.0-3.0]'), (3.0, 10.0, '量比[3.0+]')]:
    subset = df_all[(df_all['vol_ratio'] >= vol_min) & (df_all['vol_ratio'] < vol_max)]
    if len(subset) > 0:
        win_rate = subset['win'].mean() * 100
        avg_return = subset['hold_return'].mean()
        print(f'{label}: {len(subset)}笔, 胜率{win_rate:.1f}%, 均收益{avg_return:.2f}%')

# 按日期分组
print()
print('=== 按日期分组统计 (最近10个交易日) ===')
df_all_sorted = df_all.sort_values('date')
date_groups = df_all_sorted.groupby('date').agg({
    'win': ['sum', 'count'],
    'hold_return': 'mean'
}).reset_index()
date_groups.columns = ['date', 'wins', 'total', 'avg_return']
date_groups['win_rate'] = date_groups['wins'] / date_groups['total'] * 100
date_groups = date_groups.tail(10)
for _, row in date_groups.iterrows():
    print(f"{row['date']}: {int(row['total'])}笔, 胜率{row['win_rate']:.1f}%, 均收益{row['avg_return']:.2f}%")

# 保存汇总结果
df_all.to_csv('trend_entry_trades_mv100_full.csv', index=False, encoding='utf-8-sig')
print()
print('已保存完整结果到 trend_entry_trades_mv100_full.csv')
