# -*- coding: utf-8 -*-
"""市场环境与胜率分析"""
import pandas as pd

df = pd.read_csv('optimized_signals_v2.csv', encoding='utf-8-sig')

# 按月统计
df['month'] = df['date'].astype(str).str[:6]

print('=' * 70)
print('月度胜率分布')
print('=' * 70)

monthly = df.groupby('month').agg({
    'win': ['sum', 'count'],
    'hold_return': 'mean'
}).reset_index()
monthly.columns = ['month', 'wins', 'total', 'avg_return']
monthly['win_rate'] = monthly['wins'] / monthly['total'] * 100

monthly = monthly.sort_values('month')
for _, row in monthly.iterrows():
    marker = '***' if row['win_rate'] >= 60 else ('**' if row['win_rate'] >= 50 else '')
    print('%s: %d笔, 胜率%.1f%%%s, 均收益%.2f%%' % (
        row['month'], int(row['total']), row['win_rate'], marker, row['avg_return']))

# 分析高胜率月份的特征
print('\n' + '=' * 70)
print('高胜率月份(>=55%)特征分析')
print('=' * 70)

high_wr_months = monthly[monthly['win_rate'] >= 55]['month'].tolist()
if high_wr_months:
    df_high = df[df['month'].isin(high_wr_months)]
    
    print('\n高胜率月份信号特征:')
    print('  平均RSI: %.1f' % df_high['rsi6'].mean())
    print('  平均量比: %.2f' % df_high['vol_ratio'].mean())
    print('  平均20日动量: %.1f%%' % df_high['momentum'].mean())
    print('  平均评分: %.1f' % df_high['total_score'].mean())
    
    print('\n低胜率月份(<45%)特征分析')
    low_wr_months = monthly[monthly['win_rate'] < 45]['month'].tolist()
    if low_wr_months:
        df_low = df[df['month'].isin(low_wr_months)]
        print('  平均RSI: %.1f' % df_low['rsi6'].mean())
        print('  平均量比: %.2f' % df_low['vol_ratio'].mean())
        print('  平均20日动量: %.1f%%' % df_low['momentum'].mean())
        print('  平均评分: %.1f' % df_low['total_score'].mean())

# 寻找高胜率模式
print('\n' + '=' * 70)
print('寻找高胜率模式')
print('=' * 70)

# RSI区间与胜率
print('\nRSI区间 vs 胜率:')
for rsi_min, rsi_max in [(45,55), (55,60), (60,65), (65,70)]:
    subset = df[(df['rsi6'] >= rsi_min) & (df['rsi6'] < rsi_max)]
    if len(subset) > 50:
        wr = subset['win'].mean() * 100
        ar = subset['hold_return'].mean()
        print('  RSI[%d-%d]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (rsi_min, rsi_max, len(subset), wr, ar))

# 量比区间与胜率
print('\n量比区间 vs 胜率:')
for vol_min, vol_max in [(1.5,2.0), (2.0,2.5), (2.5,3.0)]:
    subset = df[(df['vol_ratio'] >= vol_min) & (df['vol_ratio'] < vol_max)]
    if len(subset) > 50:
        wr = subset['win'].mean() * 100
        ar = subset['hold_return'].mean()
        print('  量比[%.1f-%.1f]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (vol_min, vol_max, len(subset), wr, ar))

# 动量区间与胜率
print('\n20日动量区间 vs 胜率:')
for mom_min, mom_max in [(0,15), (15,25), (25,35), (35,50)]:
    subset = df[(df['momentum'] >= mom_min) & (df['momentum'] < mom_max)]
    if len(subset) > 50:
        wr = subset['win'].mean() * 100
        ar = subset['hold_return'].mean()
        print('  动量[%d%%-%d%%]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (mom_min, mom_max, len(subset), wr, ar))

# 组合条件
print('\n' + '=' * 70)
print('高胜率组合条件')
print('=' * 70)

# RSI[55-65] + 量比[1.5-2.0]
subset = df[(df['rsi6'] >= 55) & (df['rsi6'] < 65) & (df['vol_ratio'] >= 1.5) & (df['vol_ratio'] < 2.0)]
if len(subset) > 0:
    wr = subset['win'].mean() * 100
    ar = subset['hold_return'].mean()
    print('RSI[55-65] + 量比[1.5-2.0]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (len(subset), wr, ar))

# RSI[55-65] + 动量[15-30]
subset = df[(df['rsi6'] >= 55) & (df['rsi6'] < 65) & (df['momentum'] >= 15) & (df['momentum'] < 30)]
if len(subset) > 0:
    wr = subset['win'].mean() * 100
    ar = subset['hold_return'].mean()
    print('RSI[55-65] + 动量[15-30%%]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (len(subset), wr, ar))

# RSI[60-68] + 量比[1.5-2.5] + 动量[10-35]
subset = df[(df['rsi6'] >= 60) & (df['rsi6'] < 68) & (df['vol_ratio'] >= 1.5) & (df['vol_ratio'] < 2.5) & (df['momentum'] >= 10) & (df['momentum'] < 35)]
if len(subset) > 0:
    wr = subset['win'].mean() * 100
    ar = subset['hold_return'].mean()
    print('RSI[60-68] + 量比[1.5-2.5] + 动量[10-35%%]: %d笔, 胜率%.1f%%, 均收益%.2f%%' % (len(subset), wr, ar))
