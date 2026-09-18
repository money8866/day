# -*- coding: utf-8 -*-
"""强共振行 ret/规模明细（诊断用，可删）"""
import sqlite3

DB = r'd:/mystock/solo/report_daily/theme_scores.db'
conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute("""SELECT trade_date, theme, lifecycle, n_stocks, ret_5, ret_10, ret_20,
                      zt_count, up_ratio, trend_score, composite_score, gate_tier
               FROM theme_scores
               WHERE lifecycle='主升' OR (zt_count >= 8 AND up_ratio >= 75)
               ORDER BY trade_date""")
print('日期      主题       生命周期 股数 ret5   ret10  ret20  涨停 宽度  趋势 综合 门禁')
for r in cur.fetchall():
    print(f"{r[0]}  {str(r[1])[:9]:<9} {str(r[2]):<5}{int(r[3] or 0):5d} "
          f"{float(r[4] or 0):6.1f} {float(r[5] or 0):6.1f} {float(r[6] or 0):6.1f} "
          f"{int(r[7] or 0):5d} {float(r[8] or 0):5.1f} {float(r[9] or 0):5.1f} "
          f"{float(r[10] or 0):5.1f} {str(r[11])}")
conn.close()
