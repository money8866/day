# -*- coding: utf-8 -*-
import sqlite3, os
db = os.path.join(r'd:\mystock', 'cache_daily', 'tail_signal_tracker.db')
conn = sqlite3.connect(db)
cur = conn.cursor()
tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print('tables:', tables)
for t in tables:
    try:
        n = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        m = cur.execute(f"SELECT MAX(signal_date) FROM {t}").fetchone()[0]
        print(f'{t}: count={n} max_date={m}')
    except Exception as e:
        print(f'{t}: err {e}')
conn.close()
