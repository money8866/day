# -*- coding: utf-8 -*-
with open(r'C:\Users\kongx\mystock\zhongjun_backtest_v4_local_halfyear.py','r',encoding='utf-8') as f:
    lines = f.readlines()

with open(r'C:\Users\kongx\mystock\_bt_scoring.txt','w',encoding='utf-8') as out:
    for i in range(215, 245):
        out.write(f'L{i+1}: {lines[i]}')