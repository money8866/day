# -*- coding: utf-8 -*-
"""查三张窄表重复行 + 对比旧/新 loader 对分歧股票返回的历史序列差异"""
import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')

DB = r'D:\mystock\cache_daily\stock_data.db'
db = sqlite3.connect(DB)

print('=== 1. 重复 (ts_code,trade_date) 组数 ===')
for t in ['daily_cache', 'daily_basic_cache', 'adj_factor_cache']:
    dup = db.execute(
        f"SELECT COUNT(*) FROM (SELECT ts_code, trade_date FROM {t} "
        f"GROUP BY ts_code, trade_date HAVING COUNT(*)>1)"
    ).fetchone()[0]
    tot = db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f'{t}: total={tot} dup_groups={dup}')

print()
print('=== 2. 分歧股票 daily_cache 与 stk_factor_pro 逐行核对 ===')
# 300628.SZ / 002081.SZ 出现在共同事件差异列表
for code in ['300628.SZ', '002081.SZ', '300839.SZ', '605296.SH']:
    print(f'--- {code} ---')
    # stk_factor_pro 该股在 2026 窗口的日期集合
    sp_dates = db.execute(
        "SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM stk_factor_pro "
        "WHERE ts_code=? AND trade_date>='20250401'", (code,)
    ).fetchone()
    dc_dates = db.execute(
        "SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM daily_cache "
        "WHERE ts_code=? AND trade_date>='20250401'", (code,)
    ).fetchone()
    print(f'  stk_factor_pro : rows={sp_dates[0]} min={sp_dates[1]} max={sp_dates[2]}')
    print(f'  daily_cache    : rows={dc_dates[0]} min={dc_dates[1]} max={dc_dates[2]}')
    # 旧表有而新表无 / 新表有而旧表无
    only_sp = db.execute(
        "SELECT COUNT(*) FROM stk_factor_pro s WHERE s.ts_code=? AND s.trade_date>='20250401' "
        "AND NOT EXISTS (SELECT 1 FROM daily_cache d WHERE d.ts_code=s.ts_code AND d.trade_date=s.trade_date)",
        (code,)
    ).fetchone()[0]
    only_dc = db.execute(
        "SELECT COUNT(*) FROM daily_cache d WHERE d.ts_code=? AND d.trade_date>='20250401' "
        "AND NOT EXISTS (SELECT 1 FROM stk_factor_pro s WHERE s.ts_code=d.ts_code AND s.trade_date=d.trade_date)",
        (code,)
    ).fetchone()[0]
    print(f'  only_stk_factor_pro={only_sp} only_daily_cache={only_dc}')
    # 共同日期上 close/vol/amount 不一致的行数
    mism = db.execute(
        "SELECT COUNT(*) FROM stk_factor_pro s JOIN daily_cache d "
        "ON s.ts_code=d.ts_code AND s.trade_date=d.trade_date "
        "WHERE s.ts_code=? AND s.trade_date>='20250401' AND s.trade_date<='20260904' "
        "AND (ABS(IFNULL(s.close,0)-IFNULL(d.close,0))>1e-6 OR ABS(IFNULL(s.vol,0)-IFNULL(d.vol,0))>1e-6 "
        "     OR ABS(IFNULL(s.amount,0)-IFNULL(d.amount,0))>1e-6)",
        (code,)
    ).fetchone()[0]
    both = db.execute(
        "SELECT COUNT(*) FROM stk_factor_pro s JOIN daily_cache d "
        "ON s.ts_code=d.ts_code AND s.trade_date=d.trade_date "
        "WHERE s.ts_code=? AND s.trade_date>='20250401' AND s.trade_date<='20260904'",
        (code,)
    ).fetchone()[0]
    print(f'  共同日期={both} 其中 close/vol/amount 不一致={mism}')

print()
print('=== 3. adj_factor_cache 稀疏性抽查（300628.SZ 2026 区间）===')
rows = db.execute(
    "SELECT trade_date, adj_factor FROM adj_factor_cache "
    "WHERE ts_code=? AND trade_date>='20260101' ORDER BY trade_date",
    ('300628.SZ',)
).fetchall()
print(f'  2026 起共 {len(rows)} 行，前 3 后 3：')
for r in rows[:3] + rows[-3:]:
    print('   ', r)

db.close()
print('DONE')
