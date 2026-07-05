# -*- coding: utf-8 -*-
"""分析精选信号"""
import pandas as pd

df = pd.read_csv('optimized_signals.csv', encoding='utf-8-sig')

# 按评分分组分析
print('=' * 70)
print('按综合评分分组')
print('=' * 70)
for score_min, score_max in [(70, 80), (80, 90), (90, 100)]:
    subset = df[(df['total_score'] >= score_min) & (df['total_score'] < score_max)]
    if len(subset) > 0:
        wr = subset['win'].mean() * 100
        ar = subset['hold_return'].mean()
        print('评分[' + str(score_min) + '-' + str(score_max) + ']: ' + str(len(subset)) + '笔, 胜率' + f'{wr:.1f}%, 均收益' + f'{ar:.2f}%')

# 按月份分析
print('')
print('=' * 70)
print('月度表现')
print('=' * 70)
df['month'] = df['date'].astype(str).str[:6]
monthly = df.groupby('month').agg({'win': 'mean', 'hold_return': 'mean', 'ts_code': 'count'})
monthly.columns = ['win_rate', 'avg_return', 'count']
monthly['win_rate'] = monthly['win_rate'] * 100
monthly = monthly.sort_index()
for month, row in monthly.tail(10).iterrows():
    wr = row['win_rate']
    ar = row['avg_return']
    cnt = int(row['count'])
    print(str(month) + ': ' + str(cnt) + '笔, 胜率' + f'{wr:.1f}%, 均收益' + f'{ar:.2f}%')

# 高评分 vs 低评分
print('')
print('=' * 70)
print('评分阈值优化')
print('=' * 70)
for score_min in [70, 75, 80, 85, 90]:
    subset = df[df['total_score'] >= score_min]
    if len(subset) > 0:
        wr = subset['win'].mean() * 100
        ar = subset['hold_return'].mean()
        days = len(subset) // 5
        print('评分>=' + str(score_min) + ': ' + str(len(subset)) + '笔(' + str(days) + '天), 胜率' + f'{wr:.1f}%, 均收益' + f'{ar:.2f}%')

# 近期5天信号
print('')
print('=' * 70)
print('最近5天信号详情')
print('=' * 70)
df_sorted = df.sort_values('date', ascending=False)
recent_dates = df_sorted['date'].unique()[:5]
for date in recent_dates:
    day_df = df_sorted[df_sorted['date'] == date]
    print('')
    print('日期: ' + str(date))
    for _, row in day_df.iterrows():
        ret = f"{row['hold_return']:.2f}%"
        score = f"{row['total_score']:.1f}"
        rsi = f"{row['rsi6']:.1f}"
        vol = f"{row['vol_ratio']:.2f}"
        print('  ' + row['ts_code'] + '  评分:' + score + '  RSI:' + rsi + '  量比:' + vol + '  收益:' + ret)
