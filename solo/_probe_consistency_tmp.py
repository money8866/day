# -*- coding: utf-8 -*-
"""临时探查：daily_cache 与 stk_factor_pro 行情一致性 + 数据覆盖区间（用完即删）"""
import sqlite3

db = r'D:\mystock\cache_daily\stock_data.db'
con = sqlite3.connect(db)

# 1) 单股逐日对比（含换手/市值/amount/复权因子，取 stk_factor_pro 与 daily_cache 共有日期）
print('== 000001.SZ 20260105~20260130 对比 ==')
q = con.execute("""
    SELECT s.trade_date, s.open, s.high, s.low, s.close, s.pre_close, s.pct_chg,
           s.vol, s.amount, s.turnover_rate, s.total_mv, s.adj_factor,
           d.open, d.close, d.vol, d.amount, d.pre_close, d.pct_chg
    FROM stk_factor_pro s JOIN daily_cache d
      ON s.ts_code=d.ts_code AND s.trade_date=d.trade_date
    WHERE s.ts_code='000001.SZ' AND s.trade_date BETWEEN '20260105' AND '20260130'
    ORDER BY s.trade_date LIMIT 10
""").fetchall()
for r in q:
    same_o = abs(r[1]-r[12]) < 1e-9; same_c = abs(r[4]-r[13]) < 1e-9
    same_v = abs(r[7]-r[14]) < 1e-9; same_a = abs(r[8]-r[15]) < 1e-9
    print(r[0], 'openOK' if same_o else f'open {r[1]}!={r[12]}',
          'closeOK' if same_c else f'close {r[4]}!={r[13]}',
          'volOK' if same_v else f'vol {r[7]}!={r[14]}',
          'amtOK' if same_a else f'amt {r[8]}!={r[15]}',
          'toRate', r[10], 'mv', r[11], 'adj', r[9])

# 2) daily_cache 覆盖最早期（2025 前）能否支撑回测 250 日窗口
print()
print('== daily_cache 关键统计 ==')
print('daily_cache min:', con.execute('SELECT MIN(trade_date) FROM daily_cache').fetchone()[0],
      'max:', con.execute('SELECT MAX(trade_date) FROM daily_cache').fetchone()[0])
print('000001.SZ daily_cache rows 20240101~:', con.execute(
    "SELECT COUNT(*), MIN(trade_date) FROM daily_cache WHERE ts_code='000001.SZ' AND trade_date>='20240101'").fetchone())
con.close()
