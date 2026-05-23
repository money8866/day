# -*- coding: utf-8 -*-
with open(r'C:\Users\kongx\mystock\zhongjun_backtest_v4_local_halfyear.py','r',encoding='utf-8') as f:
    lines = f.readlines()

with open(r'C:\Users\kongx\mystock\_peek_out.txt','w',encoding='utf-8') as out:
    # 找主循环中检测 entry_type 的位置
    for i, l in enumerate(lines):
        if 'detect_entry_type' in l and 'score += entry_bonus' in l:
            start = max(0, i-3)
            end = min(len(lines), i+10)
            for j in range(start, end):
                out.write(f'L{j+1}: {lines[j]}')
            out.write('\n=== BREAK ===\n')