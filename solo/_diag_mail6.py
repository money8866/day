# -*- coding: utf-8 -*-
import re
G = open('t_g.html', encoding='utf-8').read()
m = re.search(r'<table>.*?</table>', G, flags=re.S)
first_table = m.group(0)
rows = re.findall(r'<tr>.*?</tr>', first_table, flags=re.S)
# rows[0] is thead header row; keep it always
header = rows[0]
data_rows = rows[1:]
print('total rows', len(rows), 'data rows', len(data_rows))

for n in [20, 30, 40, 50, 60]:
    body = '<table>' + header + ''.join(data_rows[:n]) + '</table>'
    html = '<div><h2>表%d行</h2>%s</div>' % (n, body)
    open('t_n%d.html' % n, 'w', encoding='utf-8').write(html)
    print('t_n%d.html len' % n, len(html))
