import sqlite3
conn = sqlite3.connect(r'D:\mystock\cache_daily\etf_result.db')
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print("Tables:", tables)
for t in tables:
    cursor.execute(f"SELECT count(*) FROM {t[0]}")
    print(f"  {t[0]}: {cursor.fetchone()[0]} rows")
    cursor.execute(f"PRAGMA table_info({t[0]})")
    cols = cursor.fetchall()
    print(f"  Columns: {[c[1] for c in cols]}")
    cursor.execute(f"SELECT * FROM {t[0]} LIMIT 3")
    for row in cursor.fetchall():
        print(f"    {row}")
conn.close()
