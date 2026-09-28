# -*- coding: utf-8 -*-
"""临时：核查东财概念板块「脑机接口」及其同类板块的成分股。用毕即删。"""
import theme_trend_sentiment_score as ts

df = ts.get_dc_members()
print('数据源总记录:', len(df), '｜列:', list(df.columns))
names = sorted(set(df['concept_name'].dropna()))
print('\n板块名含「脑」的:', [n for n in names if '脑' in n])
print('板块名含「神经」的:', [n for n in names if '神经' in n])

for b in ['脑机接口', '脑科学', '人脑工程', '神经科学', '人脑工程概念', '脑机接口概念']:
    sub = df[df['concept_name'] == b]
    if len(sub):
        print(f"\n[{b}] {len(sub)} 只")
        for _, r in sub.iterrows():
            print('   ', r['con_code'], '|', r.get('con_name'), '| is_industry=', r.get('is_industry'))
