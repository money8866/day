# -*- coding: utf-8 -*-
"""探测 daily_basic_cache 表结构/覆盖，并对比 stk_factor_pro 数值口径（临时探针）"""
import sqlite3
import pandas as pd

DB = r'D:\mystock\cache_daily\stock_data.db'

with sqlite3.connect(DB, timeout=30.0) as conn:
    print('== 表清单 ==')
    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        print(' ', r[0])

    print('\n== daily_basic_cache schema ==')
    for r in conn.execute("PRAGMA table_info(daily_basic_cache)"):
        print(' ', r[1], r[2])
    print('  行数:', conn.execute('SELECT COUNT(*) FROM daily_basic_cache').fetchone()[0])
    print('  覆盖:', conn.execute('SELECT MIN(trade_date), MAX(trade_date) FROM daily_basic_cache').fetchone())

    print('\n== daily_basic_cache 是否有索引 ==')
    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='daily_basic_cache'"):
        print(' ', r[0])

    print('\n== stk_factor_pro schema 长度 ==')
    cols = [r[1] for r in conn.execute("PRAGMA table_info(stk_factor_pro)")]
    print('  列数:', len(cols))

print('\n== 数值口径对比（overlap 日期 20260720，样本 6 只）==')
codes = ['000001.SZ', '600519.SH', '300750.SZ', '002594.SZ', '601318.SH', '688981.SH']
with sqlite3.connect(DB, timeout=30.0) as conn:
    for code in codes:
        s = pd.read_sql("SELECT trade_date, turnover_rate, total_mv, circ_mv, amount FROM stk_factor_pro WHERE ts_code=? AND trade_date='20260720'", conn, params=(code,))
        b = pd.read_sql("SELECT trade_date, turnover_rate, total_mv, circ_mv FROM daily_basic_cache WHERE ts_code=? AND trade_date='20260720'", conn, params=(code,))
        d = pd.read_sql("SELECT trade_date, amount FROM daily_cache WHERE ts_code=? AND trade_date='20260720'", conn, params=(code,))
        print(code)
        if not s.empty:
            r = s.iloc[0]
            print('  stk_factor_pro: turnover=%.3f total_mv=%.0f circ_mv=%.0f amount=%.0f' % (r['turnover_rate'], r['total_mv'], r['circ_mv'], r['amount']))
        if not b.empty:
            r = b.iloc[0]
            print('  daily_basic_cac: turnover=%.3f total_mv=%.0f circ_mv=%.0f' % (r['turnover_rate'], r['total_mv'], r['circ_mv']))
        if not d.empty:
            r = d.iloc[0]
            print('  daily_cache:     amount=%.0f' % r['amount'])
