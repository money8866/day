# -*- coding: utf-8 -*-
with open(r'C:\Users\kongx\mystock\zhongjun_backtest_v4_local_halfyear.py','r',encoding='utf-8') as f:
    lines = f.readlines()

with open(r'C:\Users\kongx\mystock\_bt_mainloop.txt','w',encoding='utf-8') as out:
    for i, l in enumerate(lines):
        if 'entry_type' in l and 'def ' not in l and 'return ' not in l and 'score += entry_bonus' not in l:
            start = max(0, i-5)
            end = min(len(lines), i+3)
            for j in range(start, end):
                out.write(f'L{j+1}: {lines[j]}')
            out.write('\n')