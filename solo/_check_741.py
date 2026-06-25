# -*- coding: utf-8 -*-
import pandas as pd

all_df = pd.read_csv(r'D:\mystock\solo\report_daily\bull_stocks_all.csv', encoding='utf-8')
row = all_df[all_df['code'] == '002741']
if len(row):
    r = row.iloc[0]
    print(f'光华科技: 最终分 {r["最终分"]}, theme={r["theme"]}, 主题分v2 {r["主题分v2"]}')
else:
    print('光华科技: 未在 all.csv 中找到')
    # 模糊搜
    rows = all_df[all_df['code'].astype(str).str.startswith('00274')]
    if len(rows):
        print('相近code:', rows[['code','name','最终分']].to_string())
