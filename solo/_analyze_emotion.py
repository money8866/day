# -*- coding: utf-8 -*-
# 临时分析：8/3 实盘 CSV 列对齐 + Emotion 因子问题验证
import pandas as pd
import numpy as np

df = pd.read_csv(r'd:\mystock\solo\report_daily\theme_scores_v2_20260803.csv')
print('列数:', len(df.columns), ' 行数:', len(df))
print('主题数:', len(df))

# 1) 关键列分布
cols = ['theme', 'n_stocks', 'trend_score', 'sentiment_score', 'composite_score',
        's_zt_count', 's_max_lb', 's_top1_pct', 's_mid_cap_zt_count', 's_mid_cap_strong_count',
        's_high_board_count', 's_follower_zt_count', 's_echelon_levels', 's_leader_quality',
        's_avg_vol_ratio', 's_avg_turnover', 's_resonance', 's_boom_count', 's_board_seal_rate']
print('\n== 关键列样例 ==')
print(df[cols].head(8).to_string(index=False))

# 2) 涨停质量分析：top1_pct > 9.5 但 zt_count 匹配度
print('\n== top1_pct >= 9.5 的主题（可能20cm涨停）==')
sub = df[df['s_top1_pct'] >= 9.5][['theme', 'n_stocks', 's_zt_count', 's_top1_pct', 's_max_lb']]
print(sub.to_string(index=False))

# 3) 中军因子全部为0的比例
print('\n== 中军因子为0的主题比例 ==')
print('mid_cap_zt_count==0:', int((df['s_mid_cap_zt_count'] == 0).sum()), '/', len(df))
print('mid_cap_strong_count==0:', int((df['s_mid_cap_strong_count'] == 0).sum()), '/', len(df))

# 4) 龙头质量分布
print('\n== leader_quality 分布 ==')
print(df['s_leader_quality'].describe())
print('leader_quality==0:', int((df['s_leader_quality'] == 0).sum()))

# 5) 情绪分与情绪相关列的相关性
print('\n== sentiment_score 与各因子相关性 ==')
for c in ['s_up_ratio', 's_zt_count', 's_strong_ratio', 's_avg_vol_ratio', 's_avg_turnover',
          's_median_pct', 's_resonance', 's_multi_lb_count', 's_max_lb', 's_echelon_base',
          's_leader_quality', 's_high_board_count', 's_follower_zt_count']:
    corr = df['sentiment_score'].corr(df[c])
    print(f'  {c}: {corr:+.3f}')

# 6) 8/3 情绪分高的主题特征
print('\n== 情绪分 Top6 明细 ==')
top6 = df.nlargest(6, 'sentiment_score')[['theme', 'n_stocks', 'sentiment_score', 's_zt_count',
        's_max_lb', 's_high_board_count', 's_mid_cap_zt_count', 's_follower_zt_count',
        's_leader_quality', 's_echelon_bonus', 's_avg_vol_ratio']]
print(top6.to_string(index=False))
