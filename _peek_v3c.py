# -*- coding: utf-8 -*-
with open(r'C:\Users\kongx\mystock\zhongjun_v3.py','r',encoding='utf-8') as f:
    lines = f.readlines()

with open(r'C:\Users\kongx\mystock\_v3_screen.txt','w',encoding='utf-8') as out:
    for i,l in enumerate(lines):
        if any(k in l for k in ['def screen_zhongjun', 'def ai_financial', 'MIN_TECH', 'FIN_SCORE', 'MIN_REPEAT', 'MIN_HOT']):
            for j in range(i, min(i+5,len(lines))):
                out.write(f'L{j+1}: {lines[j]}')