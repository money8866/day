# -*- coding: utf-8 -*-
"""主升/升温行明细 + lifecycle 空值按日期分布（诊断用，可删）"""
import sqlite3
from collections import Counter, defaultdict

DB = r'd:/mystock/solo/report_daily/theme_scores.db'
conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute("""SELECT trade_date, theme, lifecycle, target_state, trend_score, composite_score,
                      migration_score, up_ratio, zt_count, fund_acc, hot_percentile, gate_cap_amt,
                      gate_tier, days_strong
               FROM theme_scores WHERE lifecycle IN ('主升','升温','分歧')
               ORDER BY trade_date, lifecycle, composite_score DESC""")
print('日期      主题        生命周期 目标状态      趋势  综合  迁移   宽度  涨停  资金  热度分位 容量  门禁  持续')
for r in cur.fetchall():
    print(f"{r[0]}  {str(r[1])[:8]:<9} {r[2]:<4} {str(r[3] or '')[:6]:<7} "
          f"{float(r[4] or 0):5.1f} {float(r[5] or 0):5.1f} {float(r[6] or 0):5.1f} "
          f"{float(r[7] or 0):5.1f} {int(r[8] or 0):4d} {float(r[9] or 0):5.1f} "
          f"{float(r[10] or 0):5.0f} {float(r[11] or 0):6.1f} {str(r[12]):<5} {r[13]}")

print()
print('=== lifecycle 空值按日期 ===')
cur.execute("""SELECT trade_date,
                      SUM(CASE WHEN lifecycle IS NULL OR lifecycle='' THEN 1 ELSE 0 END) AS empty_n,
                      COUNT(*) AS total
               FROM theme_scores GROUP BY trade_date ORDER BY trade_date""")
for r in cur.fetchall():
    print(f"  {r[0]}  空 {r[1]}/{r[2]}")
conn.close()
