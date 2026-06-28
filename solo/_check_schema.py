import sqlite3
conn = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')
cols = [c[1] for c in conn.execute('PRAGMA table_info(stk_factor_pro)').fetchall()]
print('atr_bfq:', 'atr_bfq' in cols)
print('rsi_bfq_14:', 'rsi_bfq_14' in cols)
print('ma_bfq_120:', 'ma_bfq_120' in cols)
print('ma_bfq_250:', 'ma_bfq_250' in cols)
c = conn.execute("SELECT count(*) FROM stk_factor_pro WHERE ts_code='600460.SH'").fetchone()[0]
print('600460.SH rows:', c)
# also check a random stock to see max rows
c2 = conn.execute("SELECT max(cnt) FROM (SELECT count(*) cnt FROM stk_factor_pro GROUP BY ts_code)").fetchone()[0]
print('max rows across all stocks:', c2)
conn.close()
