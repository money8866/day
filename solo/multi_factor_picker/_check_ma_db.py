import os, sqlite3

ma_dir = r'D:\mystock\solo\cache_backbone_tushare'
db_file = os.path.join(ma_dir, 'market_analysis.db')
print(f"DB exists: {os.path.exists(db_file)}")

if os.path.exists(db_file):
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    # List tables
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cur.fetchall()
    print(f"Tables: {tables}")
    
    for t in tables:
        cur.execute(f"PRAGMA table_info({t[0]})")
        cols = cur.fetchall()
        print(f"\n{t[0]} columns: {[c[1] for c in cols]}")
        
        cur.execute(f"SELECT * FROM {t[0]} ORDER BY rowid DESC LIMIT 1")
        row = cur.fetchone()
        if row:
            print(f"Latest row: {row}")
    conn.close()
