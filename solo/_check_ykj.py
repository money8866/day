import sqlite3
conn = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')
df = conn.execute("SELECT trade_date, close FROM stk_factor_pro WHERE ts_code='002409.SZ' ORDER BY trade_date").fetchall()
print(f'共{len(df)}条数据')
for r in df[-10:]:
    print(r)
conn.close()