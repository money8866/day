# -*- coding: utf-8 -*-
import re
G = open('t_g.html', encoding='utf-8').read()

# K: G 去掉所有 <table>...</table>
K = re.sub(r'<table>.*?</table>', '', G, flags=re.S)
open('t_k.html', 'w', encoding='utf-8').write(K)

# L: 仅第一张 table（含外层 div）
m = re.search(r'<table>.*?</table>', G, flags=re.S)
first_table = m.group(0)
L = '<div><h2>仅第一张决策表</h2>' + first_table + '</div>'
open('t_l.html', 'w', encoding='utf-8').write(L)

# M: 第一张表的前 15 行（缩小行数）
rows = re.findall(r'<tr>.*?</tr>', first_table, flags=re.S)
M = '<div><h2>第一张表前15行</h2><table>' + ''.join(rows[:15]) + '</table></div>'
open('t_m.html', 'w', encoding='utf-8').write(M)

print('G_len', len(G), 'K_len', len(K), 'L_len', len(L), 'M_len', len(M))
print('K_has_table', '<table' in K)
print('L_rows', len(rows), 'M_rows', min(15, len(rows)))
