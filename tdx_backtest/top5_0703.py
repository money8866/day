# -*- coding: utf-8 -*-
"""精选TOP5"""
import pandas as pd

df = pd.read_csv('signals_20260703.csv', encoding='utf-8-sig')

# 精选条件
df_filtered = df[
    (df['total_score'] >= 62) &
    (df['rsi6'] >= 40) & (df['rsi6'] <= 65) &
    (df['vol_ratio'] >= 1.2) & (df['vol_ratio'] <= 2.5) &
    (df['momentum'] >= 10) & (df['momentum'] <= 40)
]

df_filtered = df_filtered.sort_values('total_score', ascending=False)

print('2026-07-03 精选TOP5:')
print('=' * 70)
print('代码             收盘价    RSI     量比     评分    20日动量')
print('=' * 70)

for i, (_, row) in enumerate(df_filtered.head(5).iterrows()):
    code = row['ts_code']
    close = '%.2f' % row['close']
    rsi = '%.1f' % row['rsi6']
    vol = '%.2f' % row['vol_ratio']
    score = '%.1f' % row['total_score']
    mom = '%.1f' % row['momentum']
    print('%s  %s   %s    %s   %s    %s%%' % (code, close, rsi, vol, score, mom))

print()
print('=' * 70)
print('筛选条件:')
print('  评分 >= 62')
print('  RSI 40-65')
print('  量比 1.2-2.5')
print('  20日动量 10%-40%')
print('=' * 70)

# 导出TOP5
df_filtered.head(5).to_csv('top5_20260703.csv', index=False, encoding='utf-8-sig')
