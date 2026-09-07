# -*- coding: utf-8 -*-
"""临时探查：daily_basic_cache 与 stk_factor_pro 的 turnover_rate/total_mv 一致性（重叠期 20260710~20260803）"""
import sqlite3

db = r'D:\mystock\cache_daily\stock_data.db'
con = sqlite3.connect(db)
rows = con.execute("""
    SELECT b.trade_date, b.ts_code, b.turnover_rate, b.volume_ratio, b.total_mv, b.circ_mv,
           s.turnover_rate, s.volume_ratio, s.total_mv, s.circ_mv
    FROM daily_basic_cache b JOIN stk_factor_pro s
      ON b.ts_code=s.ts_code AND b.trade_date=s.trade_date
    WHERE b.trade_date='20260722' LIMIT 8
""").fetchall()
print('trade_date ts_code | db_basic(to,volr,mv,circ) vs stk_factor(to,volr,mv,circ)')
for r in rows:
    same = (abs(r[2]-r[6])<1e-9) and (abs(r[4]-r[8])<1e-9)
    print(f'{r[0]} {r[1]} | {r[2]} {r[3]} {r[4]} {r[5]} vs {r[6]} {r[7]} {r[8]} {r[9]} same={same}')
# 看 daily_basic_cache 有 turnover 的完整天数/股票数
print()
print('daily_basic_cache rows/日/股:', con.execute('SELECT COUNT(*), COUNT(DISTINCT trade_date), COUNT(DISTINCT ts_code) FROM daily_basic_cache').fetchone())
con.close()
