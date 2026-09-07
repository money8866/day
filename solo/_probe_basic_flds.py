# -*- coding: utf-8 -*-
import sqlite3
DB = r'D:\mystock\cache_daily\stock_data.db'
c = sqlite3.connect(DB, timeout=60.0)
q = lambda s, *p: c.execute(s, p).fetchall()
print('stk_factor_pro 300368.SZ 20260803:', q(
    "SELECT turnover_rate, turnover_rate_f, volume_ratio, total_mv, circ_mv, adj_factor "
    "FROM stk_factor_pro WHERE ts_code=? AND trade_date=?", '300368.SZ', '20260803'))
print('daily_basic_cache 300368.SZ 20260803:', q(
    "SELECT turnover_rate, turnover_rate_f, volume_ratio, total_mv, circ_mv "
    "FROM daily_basic_cache WHERE ts_code=? AND trade_date=?", '300368.SZ', '20260803'))
print('adj_factor_cache 300368.SZ 20260803:', q(
    "SELECT adj_factor FROM adj_factor_cache WHERE ts_code=? AND trade_date=?", '300368.SZ', '20260803'))
print('adj_factor_cache 全表日分布(近6个有数据日):', q(
    "SELECT trade_date, COUNT(*) FROM adj_factor_cache GROUP BY trade_date ORDER BY trade_date DESC LIMIT 6"))
print('daily_basic_cache turnover_rate_f 非空行数(20260803):', q(
    "SELECT COUNT(*) FROM daily_basic_cache WHERE trade_date='20260803' AND turnover_rate_f IS NOT NULL"))
print('daily_basic_cache 总行数(20260803):', q(
    "SELECT COUNT(*) FROM daily_basic_cache WHERE trade_date='20260803'"))
print('daily_basic_cache 是否含列 turnover_rate_f:', q(
    "SELECT COUNT(*) FROM pragma_table_info('daily_basic_cache') WHERE name='turnover_rate_f'"))
