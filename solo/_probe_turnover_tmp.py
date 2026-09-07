# -*- coding: utf-8 -*-
"""临时探查：stk_factor_pro 的 turnover_rate 从何时起有值；daily_basic_cache 需回填起点推断"""
import sqlite3

db = r'D:\mystock\cache_daily\stock_data.db'
con = sqlite3.connect(db)
# turnover_rate 有值的起始日期（取 3 只代表性股票）
for code in ('000001.SZ', '600519.SH', '300750.SZ'):
    r = con.execute("SELECT MIN(trade_date), COUNT(*) FROM stk_factor_pro "
                    "WHERE ts_code=? AND turnover_rate IS NOT NULL AND turnover_rate>0", (code,)).fetchone()
    r2 = con.execute("SELECT MIN(trade_date), MAX(trade_date) FROM stk_factor_pro WHERE ts_code=?", (code,)).fetchone()
    print(code, 'turnover>0 起于', r, '全历史', r2)
# 单日全市场 turnover_rate 非空计数（抽查 20240115 / 20241231 / 20250630 / 20260904）
for d in ('20240115', '20241231', '20250630', '20260904'):
    c = con.execute("SELECT COUNT(*) FROM stk_factor_pro WHERE trade_date=? AND turnover_rate IS NOT NULL AND turnover_rate>0", (d,)).fetchone()[0]
    print(d, 'turnover 非空行数:', c)
con.close()
