# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd

df = pd.read_csv(r'D:\mystock\solo\output\bts\bts_backtest_20240101_20260814.csv')
w = df.nsmallest(8, 'fut_max_dd')
print('== 最大回撤前8 ==')
print(w[['ts_code', 'name', 'date', 'bts', 'grade', 'signal', 'fut5', 'fut10', 'fut20', 'fut_max_dd', 'breakout_date']].to_string())
print()
buy = df[df['grade'].isin(('S', 'A', 'B'))]
print(f'买入池最大回撤: {buy["fut_max_dd"].min():.2f}%')
w2 = buy.nsmallest(5, 'fut_max_dd')
print(w2[['ts_code', 'name', 'date', 'grade', 'signal', 'fut_max_dd', 'fut20']].to_string())
