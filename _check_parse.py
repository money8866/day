# -*- coding: utf-8 -*-
data = open(r'C:\Users\kongx\mystock\zhongjun_backtest_v3_local_halfyear.py','rb').read()
import ast
try:
    ast.parse(data.decode('utf-8'))
    print('Parse OK')
except SyntaxError as e:
    print('SyntaxError at line', e.lineno)
    lines2 = data.decode('utf-8').split('\n')
    for i in range(max(0, e.lineno-3), min(len(lines2), e.lineno+2)):
        print(f'  {i+1}: {repr(lines2[i])}')
