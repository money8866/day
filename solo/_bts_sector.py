# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r'd:\mystock\solo')
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd

df = pd.read_csv(r'd:\mystock\solo\output\bts\bts_daily_20260810.csv')
sb = pd.read_csv(r'd:\mystock\cache_daily\stock_basic.csv')
sa = df[df['grade'].isin(('S', 'A'))].copy()
sa = sa.merge(sb[['ts_code', 'industry']], on='ts_code', how='left')

# 医药类集群
pharma_ind = ['医疗保健', '化学制药', '医药商业', '生物制药', '中药', '医疗器械']
clusters = ['医疗保健', '化学制药', '医药商业', '电气设备', '软件服务', '化工原料', '汽车配件']
print('=== 主要集群 S/A 数量 / Entry均值 ===')
for cl in clusters:
    g = sa[sa['industry'] == cl]
    if len(g):
        print(f'{cl}: n={len(g)}  Entry均值 {g["entry"].mean():.1f}  最高Entry {g["entry"].max():.1f}')

print('\n=== 医药类全部 S/A（Entry 降序）===')
med = sa[sa['industry'].isin(pharma_ind)].sort_values('entry', ascending=False)
print(med[['ts_code', 'name', 'industry', 'entry', 'bts', 'grade']].to_string(index=False))
print(f'\n博济医药 300404 在医药类 S/A 中排第 {med.index[med["ts_code"]=="300404.SZ"][0]+1}/{len(med)} 位')
