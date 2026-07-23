# -*- coding: utf-8 -*-
import pandas as pd
df = pd.read_csv('D:/mystock/solo/report_daily/enhanced_timing_bull_all_20260722.csv', encoding='utf-8-sig')
print('行数:', len(df))
print('分级:', df['修正后胜率分级'].value_counts().to_dict())
print('择时分范围:', df['量化择时分'].min(), '-', df['量化择时分'].max())
sa = df[df['修正后胜率分级'].isin(['S','A'])]
print('S+A:', len(sa), '只')
for _, r in sa.head(5).iterrows():
    print('  ' + str(r['名称']) + ' ' + str(round(r['量化择时分'],1)) + '分')
