# -*- coding: utf-8 -*-
import re
G = open('t_g.html', encoding='utf-8').read()
# 统计所有 table 及其行数
tables = re.findall(r'<table>.*?</table>', G, flags=re.S)
print('total tables:', len(tables))
total_tr = 0
for i, t in enumerate(tables):
    rows = re.findall(r'<tr>', t)
    total_tr += len(rows)
    # 找该表前面最近的标题
    pos = G.find(t)
    head = G[max(0,pos-200):pos]
    hm = re.findall(r'<h\d>(.*?)</h\d>', head, flags=re.S)
    title = (hm[-1] if hm else '?')[:40]
    print(f'  table#{i}: rows={len(rows)} len={len(t)} prev_h={title!r}')
print('total <tr> in email:', total_tr)
