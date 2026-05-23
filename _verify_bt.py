# -*- coding: utf-8 -*-
with open(r'C:\Users\kongx\mystock\zhongjun_backtest_v4_local_halfyear.py','r',encoding='utf-8') as f:
    lines = f.readlines()

# 找到 high_21 和 price > high_21*0.98 的位置
for i, l in enumerate(lines):
    if 'high_21 = h.iloc[-21:-1].max()' in l:
        print(f'Line {i+1}: high_21 definition')
        # 打印接下来20行
        for j in range(i, min(i+20, len(lines))):
            print(f'  L{j+1}: {lines[j]}', end='')
        break