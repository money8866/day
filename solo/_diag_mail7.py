# -*- coding: utf-8 -*-
import re
G = open('t_g.html', encoding='utf-8').read()
m = re.search(r'<table>.*?</table>', G, flags=re.S)
first_table = m.group(0)
rows = re.findall(r'<tr>.*?</tr>', first_table, flags=re.S)
header = rows[0]
data = rows[1:]

def mk(rows_list):
    return '<table>' + header + ''.join(rows_list) + '</table>'

# 单表 80 行
open('t_p80.html','w',encoding='utf-8').write('<div><h2>单表80行</h2>'+mk(data[:80])+'</div>')
# 单表 70 行
open('t_p70.html','w',encoding='utf-8').write('<div><h2>单表70行</h2>'+mk(data[:70])+'</div>')
# 尾部 60~94 行（34 行）单独成表
open('t_tail.html','w',encoding='utf-8').write('<div><h2>尾部行60-94</h2>'+mk(data[60:94])+'</div>')
# 拆成两张表：各 47 行
two = '<div><h2>拆表A(0-46)</h2>'+mk(data[:47])+'<h2>拆表B(47-93)</h2>'+mk(data[47:94])+'</div>'
open('t_two.html','w',encoding='utf-8').write(two)
print('len p80', len(mk(data[:80])), 'p70', len(mk(data[:70])), 'tail', len(mk(data[60:94])), 'two', len(two))
