import sqlite3
import pandas as pd

# 检查 theme_portfolio.db
conn = sqlite3.connect(r'D:\mystock\solo\bak0615\cache_backbone_tushare\theme_portfolio.db')
cursor = conn.cursor()

# 查看表结构
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print(f'数据库表：{[t[0] for t in tables]}')

# 查看每个表的记录数
for table in tables:
    table_name = table[0]
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    count = cursor.fetchone()[0]
    print(f'\n表 {table_name}：{count} 条记录')
    
    # 查看前5条
    cursor.execute(f"SELECT * FROM {table_name} LIMIT 5")
    rows = cursor.fetchall()
    print(f'  示例：')
    for row in rows:
        print(f'    {row}')

conn.close()
