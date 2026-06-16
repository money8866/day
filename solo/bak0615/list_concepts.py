#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pandas as pd
df = pd.read_pickle('cache_backbone_tushare/ths_concept_members.pkl')
print('缓存中的21个概念:')
for c in sorted(df['concept_name'].unique()):
    cnt = len(df[df['concept_name'] == c])
    print(f'  {c}: {cnt} 只')