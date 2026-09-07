# -*- coding: utf-8 -*-
"""查看 300628.SZ 在 stk_factor_pro 与 daily_cache 中的具体数值差异模式"""
import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
db = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')

code = '300628.SZ'
print('=== stk_factor_pro 与 daily_cache 各日期数值 ===')
rows = db.execute("""
    SELECT s.trade_date,
           s.close, d.close,
           s.vol, d.vol,
           s.amount, d.amount,
           s.close/d.close AS c_ratio,
           s.vol/d.vol     AS v_ratio,
           s.amount/d.amount AS a_ratio,
           f.adj_factor
    FROM stk_factor_pro s
    JOIN daily_cache d ON s.ts_code=d.ts_code AND s.trade_date=d.trade_date
    LEFT JOIN adj_factor_cache f ON f.ts_code=s.ts_code AND f.trade_date=s.trade_date
    WHERE s.ts_code=? AND s.trade_date BETWEEN '20250801' AND '20260904'
    ORDER BY s.trade_date
""", (code,)).fetchall()
print(f'rows={len(rows)}')
# 打印首/中/尾 + 任何 c_ratio 变化点
prev = None
for i, r in enumerate(rows):
    cr = r[7]
    if prev is None or abs(cr - prev) > 1e-9:
        mark = ' <== ratio CHANGE'
    else:
        mark = ''
    if len(rows) - i <= 3 or i < 3 or mark:
        print(f'  {r[0]} close:{r[1]} vs {r[2]} | vol:{r[3]} vs {r[4]} | amt:{r[5]} vs {r[6]} | '
              f'c_ratio={cr:.6f} v_ratio={r[8]:.4f} a_ratio={r[9]:.4f} adj={r[10]}{mark}')
    prev = cr

print()
print('=== stk_factor_pro 该股字段名（确认其价格列/是否有 hfq/qfq 列）===')
cols = db.execute("SELECT name FROM pragma_table_info('stk_factor_pro') WHERE name IN "
                  "('close','hfq_close','qfq_close','close_hfq','close_qfq','adj_factor','pre_close')").fetchall()
print(cols)
db.close()
