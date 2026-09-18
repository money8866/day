# -*- coding: utf-8 -*-
"""三日对照：up/zt/fund/cap_state（诊断用，可删）"""
import sqlite3, json

DB = r'd:/mystock/solo/report_daily/theme_scores.db'
conn = sqlite3.connect(DB)
cur = conn.cursor()


def fetch(d):
    cur.execute("""SELECT theme, lifecycle, up_ratio, zt_count, fund_acc, composite_score,
                          trend_score, ret_5, gate_tier, gate_feat
                   FROM theme_scores WHERE trade_date=? ORDER BY composite_score DESC""", (d,))
    return {r[0]: r for r in cur.fetchall()}


A, B, C = fetch('20260915'), fetch('20260916'), fetch('20260917')
print(f"{'主题':<10}{'up 15→16→17':<24}{'zt 15→16→17':<18}{'fund 15→16→17':<26}"
      f"{'cap_state(16/17)':<26}{'tier 16/17'}")
for th in B:
    a, b, c = A.get(th), B.get(th), C.get(th)
    cs = []
    for x in (b, c):
        s = ''
        if x and x[9]:
            try:
                s = json.loads(x[9]).get('cap_state', '')
            except Exception:
                pass
        cs.append(s or '-')
    u = lambda x: f"{float(x[2] or 0):4.0f}" if x else ' -- '
    z = lambda x: f"{int(x[3] or 0):3d}" if x else ' --'
    fu = lambda x: f"{float(x[4] or 0):4.0f}" if x else ' -- '
    print(f"{th[:9]:<10}{u(a)}→{u(b)}→{u(c)}   {z(a)}→{z(b)}→{z(c)}   "
          f"{fu(a)}→{fu(b)}→{fu(c)}    {cs[0]:<12}/{cs[1]:<12} "
          f"{str(b[8]) if b else '-':<5}/{str(c[8]) if c else '-'}")
conn.close()
