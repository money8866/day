import pandas as pd
import sqlite3

db_path = 'cache_db/tdx_concept.db'
conn = sqlite3.connect(db_path)

# 检查股票日线数据
df = pd.read_sql('SELECT * FROM daily_data LIMIT 5', conn)
print('=== daily_data ===')
print(df)

# 获取总记录数
total = pd.read_sql('SELECT COUNT(*) FROM daily_data', conn)
print(f'\n总记录数: {total.iloc[0, 0]}')

conn.close()
