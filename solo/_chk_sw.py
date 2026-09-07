# -*- coding: utf-8 -*-
import sqlite3, os
paths = [r'd:\mystock\cache_daily\stock_data.db', r'd:\mystock\cache_daily\stock_cache.db']
for p in paths:
    if not os.path.exists(p):
        print('missing', p)
        continue
    try:
        con = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
        tabs = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        print('==', p, 'tables:', len(tabs))
        for t in tabs:
            tl = t.lower()
            if any(k in tl for k in ['sw', 'ind', 'member', 'basic', 'concept', 'theme', 'plate', 'sector', 'stock_basic']):
                try:
                    n = con.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
                    cols = [c[1] for c in con.execute('PRAGMA table_info("%s")' % t)][:16]
                    print('   ', t, 'rows=', n)
                    print('       cols=', cols)
                except Exception as e:
                    print('   ', t, 'err', e)
        con.close()
    except Exception as e:
        print(p, 'skip', e)
