"""Debug v8 - check actual column names"""
import sqlite3

DB = r'D:\mystock\cache_daily\stock_data.db'

conn = sqlite3.connect(DB)
cursor = conn.cursor()

# Get column names
cursor.execute("PRAGMA table_info(stk_factor_pro)")
columns = cursor.fetchall()
print("Columns in stk_factor_pro:")
for col in columns:
    print(f"  {col[1]} ({col[2]})")

conn.close()
