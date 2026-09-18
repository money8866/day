# -*- coding: utf-8 -*-
"""定位 daily.py 中第一梯队池的构造位置。"""
import io
p = r'd:\mystock\solo\hvt_bull\daily.py'
lines = io.open(p, 'r', encoding='utf-8').read().splitlines()
print('总行数:', len(lines))
print()
for i, l in enumerate(lines, 1):
    if any(k in l for k in ('第一梯队', 'echelon', 'PRIMARY_BUY', 'primary_pool', 'result[')):
        print(f'L{i}: {l.rstrip()[:150]}')
