# -*- coding: utf-8 -*-
import pandas as pd

df = pd.read_csv('output/bull_all_20260622_213151.csv')

# 找宏和科技和杰普特
for name in ['宏和科技', '杰普特']:
    stock = df[df['name'] == name]
    if len(stock) > 0:
        print(f"{name}: ts_code={stock.iloc[0]['ts_code']}, theme={stock.iloc[0]['theme']}")
    else:
        print(f"{name}: not found")

# 也用代码搜一下
for code in ['301556', '688025', '宏和', '杰普']:
    stock = df[df['ts_code'].str.contains(code) | df['name'].str.contains(code)]
    if len(stock) > 0:
        print(f"Found {code}: {stock[['name', 'ts_code', 'theme']].to_string()}")
