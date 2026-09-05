# -*- coding: utf-8 -*-
import csv, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

src = r'D:\mystock\solo\sli\output\sli_v2_subsector_top5_20260901.csv'
rows = []
with open(src, encoding='utf-8-sig') as f:
    rd = csv.DictReader(f)
    for r in rd:
        rows.append(r)

print('总数据行:', len(rows))
# 行业列表及行数
from collections import Counter
c = Counter(r['三级行业'] for r in rows)
print('行业数:', len(c))
for k, v in c.items():
    print(f'  {k}: {v}行')
print('列名:', list(rows[0].keys()))
