# -*- coding: utf-8 -*-
import sqlite3, os, sys
LOG = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '_v2_probe.log'),
           'w', encoding='utf-8')
sys.stdout = LOG
c = sqlite3.connect(r'd:\mystock\cache_daily\stock_data.db')
tabs = [r[0] for r in c.execute("select name from sqlite_master where type='table'")]
print('tables:', tabs)
for t in tabs:
    cols = [r[1] for r in c.execute(f'PRAGMA table_info({t})')]
    print(t, cols)
    try:
        rows = list(c.execute(f"select * from {t} where ts_code='603003.SH' limit 1"))
        if rows:
            print('  HIT', rows)
    except Exception as e:
        print('  err', e)
LOG.close()
