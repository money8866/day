# -*- coding: utf-8 -*-
import sqlite3, os

# Check all .db files in mystock
mystock = r'C:\Users\kongx\mystock'
for fname in os.listdir(mystock):
    if fname.endswith('.db'):
        fpath = os.path.join(mystock, fname)
        try:
            conn = sqlite3.connect(fpath)
            c = conn.cursor()
            c.execute("SELECT name FROM sqlite_master")
            tables = [r[0] for r in c.fetchall()]
            print(f'{fname}: tables={tables}')
            if 'hot_sector' in tables:
                c.execute("SELECT COUNT(*) FROM hot_sector")
                print(f'  hot_sector rows: {c.fetchone()[0]}')
                c.execute("SELECT MAX(date) FROM hot_sector")
                print(f'  latest date: {c.fetchone()[0]}')
                c.execute("SELECT l2_name, momentum FROM hot_sector ORDER BY date DESC LIMIT 3")
                print(f'  sample: {c.fetchall()}')
            conn.close()
        except Exception as e:
            print(f'{fname}: error - {e}')
