"""清理SQLite行业缓存"""
import sqlite3
db_path = r'd:\mystock\solo\cache\eld\eld_cache.sqlite'
conn = sqlite3.connect(db_path)
c = conn.cursor()
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
for t in tables:
    name = t[0]
    if 'industry' in name.lower():
        count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        conn.execute(f'DELETE FROM "{name}"')
        print(f"Cleared {name}: {count} rows")
conn.commit()
conn.close()
print("Done")
