#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pandas as pd
df = pd.read_pickle('cache_backbone_tushare/ths_concept_members.pkl')
print('概念缓存记录数:', len(df))
print('唯一概念数:', df['concept_name'].nunique())
print()

ai_concepts = ['AI芯片', '国产GPU', '算力芯片', '昇腾生态', 'GPU', 'NPU']
for c in ai_concepts:
    cnt = len(df[df['concept_name'] == c])
    print(f'{c}: {cnt} 只股票')

print()
print('概念名称包含GPU的:')
for c in df['concept_name'].unique():
    if 'GPU' in c or 'gpu' in c.lower():
        print(f'  {c}: {len(df[df["concept_name"]==c])} 只')

print()
print('概念名称包含芯片的:')
for c in df['concept_name'].unique():
    if '芯片' in c:
        print(f'  {c}: {len(df[df["concept_name"]==c])} 只')