# -*- coding: utf-8 -*-
"""0917 gate_feat 快照检查（诊断用，可删）"""
import sqlite3, json
DB = r'd:/mystock/solo/report_daily/theme_scores.db'
conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute("""SELECT theme, lifecycle, ret_5, gate_tier, days_strong, gate_feat
               FROM theme_scores WHERE trade_date='20260917' ORDER BY composite_score DESC""")
for r in cur.fetchall():
    try:
        f = json.loads(r[5] or '{}')
    except Exception:
        f = {}
    print(f"{str(r[0])[:9]:<10} lc={str(r[1]):<6} ret_5列={r[2]:<7} tier={str(r[3]):<5} "
          f"days={r[4]} | feat.ret5={f.get('ret5')} feat.days={f.get('days')} "
          f"feat.up={f.get('up')} feat.cap={f.get('cap')} feat.cap_state={f.get('cap_state')}")
conn.close()
