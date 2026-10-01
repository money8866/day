# -*- coding: utf-8 -*-
import sqlite3, os, glob
d = r'D:\mystock\cache_daily'
print('目录:', d, os.path.isdir(d))
for f in os.listdir(d)[:40]:
    p = os.path.join(d, f)
    print('  %-32s %10.1f MB' % (f, os.path.getsize(p) / 1048576))
print()
p = os.path.join(d, 'stock_data.db')
con = sqlite3.connect('file:%s?mode=ro' % p.replace('\\', '/'), uri=True)
print('=== stock_data.db 表 ===')
for r in con.execute("select name, type from sqlite_master where type in ('table','view')"):
    print('  ', r[0], r[1])
for t in ('index_daily', 'index_data', 'daily', 'stock_daily'):
    try:
        cols = [r[1] for r in con.execute('pragma table_info(%s)' % t)]
        if cols:
            n = con.execute('select count(*) from %s' % t).fetchone()[0]
            print('  ', t, n, cols[:14])
    except Exception as e:
        pass
con.close()
