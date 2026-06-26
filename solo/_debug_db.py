import sqlite3, pandas as pd

DB_PATH = r"D:\mystock\cache_daily\stock_data.db"
conn = sqlite3.connect(DB_PATH)

# 查最新日期
latest = pd.read_sql_query("SELECT MAX(trade_date) as max_date, MIN(trade_date) as min_date FROM stk_factor_pro", conn)
print("全库日期范围:", latest.to_string())

# 各股票最新日期
stock_dates = pd.read_sql_query("""
    SELECT ts_code, MAX(trade_date) as max_date, MIN(trade_date) as min_date, COUNT(*) as cnt
    FROM stk_factor_pro WHERE ts_code IN ('600498.SH','688003.SH','002409.SZ','600460.SH','002747.SZ')
    GROUP BY ts_code
""", conn)
print("\n目标股票:", stock_dates.to_string())

conn.close()
