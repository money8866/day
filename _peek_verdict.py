# -*- coding: utf-8 -*-
with open(r'C:\Users\kongx\mystock\zhongjun_v4.py','r',encoding='utf-8') as f:
    lines = f.readlines()

with open(r'C:\Users\kongx\mystock\_verdict.txt','w',encoding='utf-8') as out:
    for i, l in enumerate(lines):
        if 'def ai_generate_verdict' in l:
            for j in range(i, min(i+80, len(lines))):
                out.write(f'L{j+1}: {lines[j]}')
            break