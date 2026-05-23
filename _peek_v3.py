# -*- coding: utf-8 -*-
with open(r'C:\Users\kongx\mystock\zhongjun_backtest_v3.py','r',encoding='utf-8') as f:
    lines = f.readlines()

with open(r'C:\Users\kongx\mystock\_v3_keys.txt','w',encoding='utf-8') as out:
    for i,l in enumerate(lines):
        out.write(f'L{i+1}: {l}')