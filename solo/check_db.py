import sqlite3

conn = sqlite3.connect('d:/mystock/solo/cache_backbone_tushare/market_analysis.db')
cursor = conn.cursor()

cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in cursor.fetchall()]
print("Tables:", tables)

for table in tables:
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [col[1] for col in cursor.fetchall()]
    print(f"\n{table} columns:", columns)
    
    cursor.execute(f"SELECT * FROM {table} LIMIT 3")
    rows = cursor.fetchall()
    if rows:
        print(f"Sample data ({len(rows)} rows):")
        for row in rows:
            print(f"  {row}")

conn.close()