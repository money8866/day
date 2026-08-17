import sqlite3
c = sqlite3.connect(r'd:\mystock\cache_daily\stock_data.db')
print('today:', c.execute("SELECT COUNT(1) FROM stk_factor_pro WHERE trade_date='20260817'").fetchone()[0])
print('prev :', c.execute("SELECT trade_date, COUNT(1) FROM stk_factor_pro WHERE trade_date<'20260817' GROUP BY trade_date ORDER BY trade_date DESC LIMIT 1").fetchone())
c.close()
