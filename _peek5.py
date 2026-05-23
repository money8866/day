# -*- coding: utf-8 -*-
with open(r'C:\Users\kongx\mystock\zhongjun_v4.py','r',encoding='utf-8') as f:
    lines = f.readlines()

with open(r'C:\Users\kongx\mystock\_peek_out.txt','w',encoding='utf-8') as out:
    for i in range(399, 438):
        out.write(f'L{i+1}: {lines[i]}')