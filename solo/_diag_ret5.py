# -*- coding: utf-8 -*-
"""ret_5 覆盖度检查（诊断用，可删）"""
import sqlite3
DB = r'd:/mystock/solo/report_daily/theme_scores.db'
conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute("""SELECT trade_date, COUNT(*),
                      SUM(CASE WHEN ret_5 IS NULL OR ret_5=0 THEN 1 ELSE 0 END),
                      MIN(ret_5), MAX(ret_5)
               FROM theme_scores GROUP BY trade_date ORDER BY trade_date DESC LIMIT 12""")
print('日期      总数 ret5=空/0  min    max')
for r in cur.fetchall():
    print(f"{r[0]}  {r[1]:>4}  {r[2]:>6}   {r[3]}  {r[4]}")
conn.close()
