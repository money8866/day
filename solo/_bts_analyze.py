# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd

df = pd.read_csv(r'D:\mystock\solo\output\bts\bts_backtest_20240101_20260814.csv')
g = df[(df['bts'] >= 50) & (df['bts'] < 60)]
print('== [50,60) 档构成 ==')
print('signal分布:')
print(g['signal'].value_counts().to_string())
print()
print('grade分布:')
print(g['grade'].value_counts().to_string())
print()
print('buy_point分布:')
print(g['buy_point'].value_counts().to_string())
print()
print('按signal的fut5均值:')
print(g.groupby('signal')['fut5'].mean().round(2).to_string())
print()
print('按grade的fut5均值:')
print(g.groupby('grade')['fut5'].mean().round(2).to_string())
print()
print('样例(收益最高的10只):')
s = g.sort_values('fut5', ascending=False).head(10)
print(s[['ts_code', 'name', 'date', 'bts', 'entry', 'grade', 'signal', 'buy_point', 'fut5', 'fut20']].to_string())
