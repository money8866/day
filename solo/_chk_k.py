# -*- coding: utf-8 -*-
import sqlite3
import pandas as pd
con = sqlite3.connect(r"D:\mystock\cache_daily\stock_data.db")
for code in ("688332.SH", "002384.SZ", "603986.SH"):
    g = pd.read_sql(
        "select trade_date, open, high, low, close, pct_chg, vol "
        "from daily_cache where ts_code=? order by trade_date desc limit 22",
        con, params=[code])
    print("===", code, "===")
    print(g.iloc[::-1].to_string(index=False))
con.close()
