# -*- coding: utf-8 -*-
with open(r'C:\Users\kongx\mystock\_v3_keys.txt','r',encoding='utf-8') as f:
    lines = f.readlines()

with open(r'C:\Users\kongx\mystock\_v3_keys2.txt','w',encoding='utf-8') as out:
    for i,l in enumerate(lines):
        if i >= 40:
            out.write(f'L{i+1}: {l}')