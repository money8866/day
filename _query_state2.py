import sqlite3, sys
db = r'D:\mystock\report_daily\etf_result.db'
conn = sqlite3.connect(db)
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print('Tables:', [r[0] for r in cur.fetchall()])
for table in ['momentum_state', 'momentum_log', 'portfolio']:
    try:
        cur.execute(f'SELECT * FROM {table} ORDER BY 1')
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        print(f'\n=== {table} ({len(rows)} rows) ===')
        print('Cols:', cols)
        for r in rows: print(r)
    except Exception as e:
        print(f'\n{table}: {e}')
conn.close()
