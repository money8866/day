# -*- coding: utf-8 -*-
import sqlite3

con = sqlite3.connect(r'D:\mystock\solo\report_daily\er20_v22_scores.db')
cur = con.cursor()
cols = [c[1] for c in cur.execute('PRAGMA table_info(er20_v22_scores)').fetchall()]
row = cur.execute("SELECT * FROM er20_v22_scores WHERE ts_code='600500.SH' ORDER BY scan_date DESC LIMIT 5").fetchall()
print('列数:', len(cols))
for r in row:
    print('---')
    for c, v in zip(cols, r):
        print(f'{c}: {v}')
con.close()
