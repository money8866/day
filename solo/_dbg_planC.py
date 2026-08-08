# -*- coding: utf-8 -*-
"""debug: code格式与旧榜结构"""
import pandas as pd
new = pd.read_csv(r'd:\mystock\solo\report_daily\bull_stocks_all.csv')
old = pd.read_csv(r'd:\mystock\solo\report_daily\double_score_20260808_154444.csv')
print('NEW code样例:', list(new['code'].astype(str).head(5)))
print('OLD 列名:', list(old.columns))
print('OLD 前5行:')
print(old.head(5).to_string(index=False))
def norm(c):
    return str(c).strip().split('.')[0]
new_codes = set(new['code'].map(norm))
old_codes = set(old.iloc[:, 1].map(norm))
print('\nnew code总数:', len(new_codes), 'old code总数:', len(old_codes))
print('new样例(norm):', list(new['code'].map(norm).head(5)))
print('old样例(norm):', list(old.iloc[:,1].map(norm).head(5)))
print('重叠:', len(new_codes & old_codes))
