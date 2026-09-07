# -*- coding: utf-8 -*-
"""核验 stk_factor_pro 空洞日期在 daily_cache 是否为真实交易日"""
import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
db = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')

checks = [('000019.SZ', '20250103'), ('000019.SZ', '20250110'),
          ('000019.SZ', '20260716'), ('300628.SZ', '20260716'),
          ('600298.SH', '20260716'), ('002081.SZ', '20250106')]
for code, d in checks:
    row = db.execute(
        "SELECT trade_date, open, high, low, close, vol, amount, pct_chg FROM daily_cache "
        "WHERE ts_code=? AND trade_date=?", (code, d)).fetchone()
    sp = db.execute(
        "SELECT COUNT(*) FROM stk_factor_pro WHERE ts_code=? AND trade_date=?", (code, d)).fetchone()[0]
    # 前后交易日（daily_cache）
    around = db.execute(
        "SELECT trade_date, close, vol FROM daily_cache WHERE ts_code=? AND trade_date BETWEEN ? AND ? "
        "ORDER BY trade_date", (code, str(int(d) - 8), str(int(d) + 8))).fetchall()
    if row:
        print(f'{code} {d}: daily存在 close={row[4]} vol={row[5]} pct={row[7]} | stk_factor_pro行数={sp}')
    else:
        print(f'{code} {d}: daily无此日 | stk_factor_pro行数={sp}')
    print(f'   邻近日线: {[(r[0], r[1], r[2]) for r in around][:6]}...')
print()
# stk_factor_pro 缺失 20260716 的普遍性：该日全市场 stk_factor_pro 行数 vs daily 行数
for d in ['20250103', '20260716', '20260615']:
    n_sp = db.execute("SELECT COUNT(*) FROM stk_factor_pro WHERE trade_date=?", (d,)).fetchone()[0]
    n_dc = db.execute("SELECT COUNT(*) FROM daily_cache WHERE trade_date=?", (d,)).fetchone()[0]
    print(f'{d}: stk_factor_pro全市场={n_sp}  daily全市场={n_dc}')
db.close()
