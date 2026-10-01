# -*- coding: utf-8 -*-
import sqlite3
con = sqlite3.connect('file:d:/mystock/solo/picks_db/stock_picks.db?mode=ro', uri=True)
for t in ('stock_pick', 'pick_tracking'):
    print('===', t, '===')
    for r in con.execute('pragma table_info(%s)' % t):
        print('  ', r[1], r[2])
print('=== 视图 ===')
for r in con.execute("select name from sqlite_master where type='view'"):
    print('  ', r[0])
con.close()
