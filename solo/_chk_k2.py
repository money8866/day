# -*- coding: utf-8 -*-
import sqlite3
import pandas as pd

con = sqlite3.connect(r"D:\mystock\cache_daily\stock_data.db")
for code in ("300475.SZ", "688381.SH", "300666.SZ", "600183.SH"):
    g = pd.read_sql(
        "select trade_date, close, high, low, pct_chg, vol from daily_cache "
        "where ts_code=? order by trade_date desc limit 16",
        con, params=[code])
    g = g.iloc[::-1]
    print(f"=== {code} ===")
    print(g.to_string(index=False))
    print()
con.close()
