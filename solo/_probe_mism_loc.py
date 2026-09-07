# -*- coding: utf-8 -*-
"""定位 stk_factor_pro vs daily_cache 不一致的具体日期与字段"""
import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
db = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')

for code in ['300628.SZ', '002081.SZ']:
    print(f'===== {code} =====')
    rows = db.execute("""
        SELECT s.trade_date, s.close, d.close, s.vol, d.vol, s.amount, d.amount
        FROM stk_factor_pro s
        JOIN daily_cache d ON s.ts_code=d.ts_code AND s.trade_date=d.trade_date
        WHERE s.ts_code=? AND s.trade_date BETWEEN '20250301' AND '20260904'
        ORDER BY s.trade_date
    """, (code,)).fetchall()
    mism = [r for r in rows
            if abs((r[1] or 0) - (r[2] or 0)) > 1e-6
            or abs((r[3] or 0) - (r[4] or 0)) > 1e-6
            or abs((r[5] or 0) - (r[6] or 0)) > 1e-6]
    print(f'共同行={len(rows)} 不符行={len(mism)}')
    # 按月聚合
    from collections import Counter
    c = Counter(r[0][:6] for r in mism)
    for k in sorted(c):
        print(f'   {k}: {c[k]}')
    # 打印前 8 个不符行的详情
    for r in mism[:8]:
        print(f'   {r[0]} close {r[1]} vs {r[2]} | vol {r[3]} vs {r[4]} | amt {r[5]} vs {r[6]}')
    print()
db.close()
