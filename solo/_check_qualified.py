# -*- coding: utf-8 -*-
import pandas as pd

df = pd.read_csv(r'D:\mystock\solo\report_daily\bull_stocks_qualified.csv', encoding='utf-8')
print(f'合格池: {len(df)} 只')
print(f'评分范围: {df["最终分"].min():.1f} ~ {df["最终分"].max():.1f}')

# 查找时代新材和光华科技
for code_val in ['600458', '002741']:
    row = df[df['code'].astype(str) == code_val]
    if len(row):
        r = row.iloc[0]
        print(f'{r["code"]} {r["name"]}: 最终分 {r["最终分"]}, 主题 {r["theme"]}, 主题分v2 {r["主题分v2"]}')
    else:
        row = df[df['code'].astype(str).str.startswith(code_val)]
        if len(row):
            r = row.iloc[0]
            print(f'{r["code"]} {r["name"]}: 最终分 {r["最终分"]}, 主题 {r["theme"]}')
        else:
            print(f'{code_val}: 未找到')
