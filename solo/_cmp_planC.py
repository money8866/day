# -*- coding: utf-8 -*-
"""方案C 最终验证: 新榜 + 新旧重叠"""
import pandas as pd

new = pd.read_csv(r'd:\mystock\solo\report_daily\bull_stocks_all.csv')
print('新榜行数:', len(new))

new = new.sort_values('最终分', ascending=False)
print('\n新榜Top10 (方案C):')
for _, r in new.head(10).iterrows():
    print(' ', str(r['code']).zfill(6), r['name'], round(r['最终分'], 1), '| 主题:', r['theme'], '|', r['主题匹配方式'])

print('\n主题匹配方式分布:')
print(new['主题匹配方式'].fillna('(空)').value_counts().to_string())

def norm(c):
    return str(c).strip().split('.')[0].zfill(6)

old = pd.read_csv(r'd:\mystock\solo\report_daily\double_score_20260808_154444.csv')
old_codes = set(old['代码'].map(norm))
new_codes = set(new['code'].map(norm))
overlap = old_codes & new_codes
print(f'\n旧861基线 ∩ 新版: {len(overlap)}/{len(old_codes)} ({len(overlap)/len(old_codes)*100:.0f}%)')
top20_new = set(new.head(20)['code'].map(norm))
print(f'新Top20 ∩ 旧榜: {len(top20_new & old_codes)}/20')
print('新Top20代码:', sorted(top20_new))
