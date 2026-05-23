# -*- coding: utf-8 -*-
with open(r'C:\Users\kongx\mystock\zhongjun_v4.py','r',encoding='utf-8') as f:
    lines = f.readlines()

# Show lines 368-430 (screen_zhongjun function body)
for i in range(368, 432):
    print(f'L{i+1}: {lines[i]}', end='')