import sqlite3, pandas as pd

DB_PATH = r"D:\mystock\cache_daily\stock_data.db"
conn = sqlite3.connect(DB_PATH)

stocks = ['600498.SH','688003.SH','002409.SZ','600460.SH','002747.SZ']
for code in stocks:
    df = pd.read_sql_query(
        "SELECT * FROM stk_factor_pro WHERE ts_code = ? ORDER BY trade_date DESC LIMIT 180",
        conn, params=(code,)
    )
    df_sorted = df.sort_values('trade_date').reset_index(drop=True)
    print(f"{code}: LIMIT180后=行数{len(df_sorted)}, 首={df_sorted.iloc[0]['trade_date']}, 尾={df_sorted.iloc[-1]['trade_date']}")
    print(f"  tail(120)后=行数{len(df_sorted.tail(120))}, 首={df_sorted.tail(120).iloc[0]['trade_date']}, 尾={df_sorted.tail(120).iloc[-1]['trade_date']}")

conn.close()
