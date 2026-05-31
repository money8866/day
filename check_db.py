# -*- coding: utf-8 -*-
import sqlite3

for db in ['cache_db/dc_concept.db', 'cache_db/tdx_concept.db']:
    print(f'=== {db} ===')
    try:
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cur.fetchall()
        for t in tables:
            name = t[0]
            cur.execute(f'SELECT COUNT(*) FROM [{name}]')
            cnt = cur.fetchone()[0]
            cur.execute(f'PRAGMA table_info([{name}])')
            cols = [r[1] for r in cur.fetchall()]
            print(f'TABLE:{name} ROWS:{cnt} COLS:{cols[:8]}')

            # sample first 3 rows
            cur.execute(f'SELECT * FROM [{name}] LIMIT 3')
            rows = cur.fetchall()
            for row in rows:
                print(f'  ROW: {str(row)[:200]}')
        conn.close()
    except Exception as e:
        print(f'ERR:{e}')
    print()