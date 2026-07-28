"""清理SQLite中财务数据的缓存"""
import sqlite3, os
db_path = r'd:\mystock\solo\cache\eld\eld_cache.sqlite'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    tables = c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    for t in tables:
        name = t[0]
        if 'finan' in name.lower() or 'financial' in name.lower():
            count = c.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            print(f'{name}: {count}条 → 删除')
            c.execute(f'DELETE FROM "{name}"')
    conn.commit()
    conn.close()
    print('已完成')
else:
    print('SQLite缓存不存在')
