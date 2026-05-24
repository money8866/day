# -*- coding: utf-8 -*-
with open(r'C:\Users\kongx\mystock\zhongjun_backtest_v4_local_halfyear.py','r',encoding='utf-8') as f:
    lines = f.readlines()

with open(r'C:\Users\kongx\mystock\_v4hot.txt','w',encoding='utf-8') as out:
    for i,l in enumerate(lines):
        if any(k in l for k in ['def load_hot', 'def get_hot', 'hot_sector', 'cache_db', '.db', 'sqlite3']):
            for j in range(i, min(i+10, len(lines))):
                out.write(f'L{j+1}: {lines[j]}')