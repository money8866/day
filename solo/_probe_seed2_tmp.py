# -*- coding: utf-8 -*-
"""临时探查：daily_cache 近日覆盖 / 宽表 adj_factor 有效性 / meta keys"""
import sqlite3
con = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')
print("daily_cache 最后8日:")
for d, c in con.execute("SELECT trade_date, COUNT(*) FROM daily_cache GROUP BY trade_date ORDER BY trade_date DESC LIMIT 8").fetchall():
    print(" ", d, c)
print("\n宽表 20260831 是否有 adj_factor 非空:")
r = con.execute("SELECT COUNT(*), COUNT(adj_factor) FROM stk_factor_pro WHERE trade_date='20260831'").fetchone()
print(r)
print("样例:", con.execute("SELECT ts_code, adj_factor FROM stk_factor_pro WHERE trade_date='20260831' AND adj_factor IS NOT NULL LIMIT 3").fetchall())
print("\ncache_meta keys:")
for k, v in con.execute("SELECT key, value FROM cache_meta").fetchall():
    print(" ", k, "=", v)
con.close()
