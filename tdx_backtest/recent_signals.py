# -*- coding: utf-8 -*-
"""最近5天信号查询"""
import pandas as pd

# 读取数据
df = pd.read_csv('trend_entry_trades_mv100_full.csv', encoding='utf-8-sig')

# 按日期排序，取最近5天
df = df.sort_values('date', ascending=False)
recent_dates = df['date'].drop_duplicates().head(5).tolist()
print('最近5个交易日:')
for d in recent_dates:
    print(int(d))

# 按日期分组统计
print('')
print('按日期统计:')
for date in recent_dates:
    subset = df[df['date'] == date]
    wins = int(subset['win'].sum())
    total = len(subset)
    wr = wins / total * 100 if total > 0 else 0
    ar = subset['hold_return'].mean()
    print(str(int(date)) + ': ' + str(total) + '笔信号, 胜率' + f'{wr:.1f}%, 均收益' + f'{ar:.2f}%')

print('')
print('=' * 70)
print('最近5天全部信号 (按收益排序)')
print('=' * 70)

# 筛选最近5天信号
df_recent = df[df['date'].isin(recent_dates)]

# 输出格式
print('日期         代码             收盘价    RSI6    量比      5日收益')
print('-' * 70)

for date in recent_dates:
    subset = df[df['date'] == date].sort_values('hold_return', ascending=False)
    for _, row in subset.head(10).iterrows():
        date_str = str(int(row['date']))
        code = row['ts_code']
        close = f"{row['close']:.2f}"
        rsi = f"{row['rsi6']:.1f}"
        vol = f"{row['vol_ratio']:.2f}"
        ret = f"{row['hold_return']:.2f}%"
        print(date_str + '  ' + code + '  ' + close + '    ' + rsi + '    ' + vol + '    ' + ret)
    print('')

# 胜率统计
print('=' * 70)
print('最近5天信号汇总')
print('=' * 70)
total_signals = len(df_recent)
total_wins = int(df_recent['win'].sum())
wr = total_wins / total_signals * 100 if total_signals > 0 else 0
ar = df_recent['hold_return'].mean()
print('总信号数: ' + str(total_signals))
print('总胜率: ' + f'{wr:.1f}%')
print('均收益: ' + f'{ar:.2f}%')

# 按RSI分组
print('')
print('按RSI分组:')
for rsi_min, rsi_max in [(40, 50), (50, 60), (60, 70), (70, 75)]:
    subset = df_recent[(df_recent['rsi6'] >= rsi_min) & (df_recent['rsi6'] < rsi_max)]
    if len(subset) > 0:
        wr = subset['win'].mean() * 100
        ar = subset['hold_return'].mean()
        print('RSI[' + str(rsi_min) + '-' + str(rsi_max) + ']: ' + str(len(subset)) + '笔, 胜率' + f'{wr:.1f}%, 均收益' + f'{ar:.2f}%')
