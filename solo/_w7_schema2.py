# -*- coding: utf-8 -*-
import sqlite3
c = sqlite3.connect('file:D:/mystock/cache_daily/stock_data.db?mode=ro', uri=True)
for r in c.execute("select name, sql from sqlite_master where type='table'"):
    print(r[0])
    print('   ', (r[1] or '').replace('\n', ' ')[:300])
c.close()
