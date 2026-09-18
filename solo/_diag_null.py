# -*- coding: utf-8 -*-
"""历史行字段完整度检查（诊断用，可删）"""
import sqlite3
DB = r'd:/mystock/solo/report_daily/theme_scores.db'
conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute("""SELECT trade_date,
                      SUM(CASE WHEN fund_acc IS NULL OR fund_acc=0 THEN 1 ELSE 0 END),
                      SUM(CASE WHEN gate_cap_amt IS NULL OR gate_cap_amt=0 THEN 1 ELSE 0 END),
                      SUM(CASE WHEN ret_5 IS NULL THEN 1 ELSE 0 END),
                      SUM(CASE WHEN composite_score IS NULL THEN 1 ELSE 0 END),
                      COUNT(*)
               FROM theme_scores GROUP BY trade_date ORDER BY trade_date""")
print('日期       fund=空/0  cap=空/0  ret5空  comp空  总数')
for r in cur.fetchall():
    print(f"{r[0]}   {r[1]:>5}     {r[2]:>5}    {r[3]:>4}   {r[4]:>4}   {r[5]}")
conn.close()
