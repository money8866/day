# -*- coding: utf-8 -*-
"""某日主题全量明细 + 上一日对照（诊断用，可删）"""
import sqlite3, sys, json

DB = r'd:/mystock/solo/report_daily/theme_scores.db'
dt = sys.argv[1] if len(sys.argv) > 1 else '20260917'
prev_dt = sys.argv[2] if len(sys.argv) > 2 else '20260916'
conn = sqlite3.connect(DB)
cur = conn.cursor()


def fetch(d):
    cur.execute("""SELECT theme, lifecycle, target_state, trend_score, composite_score,
                          migration_score, up_ratio, zt_count, fund_acc, hot_percentile,
                          gate_cap_amt, gate_tier, days_strong, gate_feat
                   FROM theme_scores WHERE trade_date=? ORDER BY composite_score DESC""", (d,))
    return {r[0]: r for r in cur.fetchall()}


P, C = fetch(prev_dt), fetch(dt)
print(f"{'主题':<10}{'LC(前→今)':<14}{'综合(前→今)':<16}{'趋势(前→今)':<16}"
      f"{'涨停(前→今)':<14}{'宽度今':<8}{'资金今':<8}{'门禁今':<6}{'持续':<5}{'目标状态今'}")
for th, r in C.items():
    p = P.get(th)
    pl = p[1] if p else '—'
    pc = float(p[4] or 0) if p else 0
    pt = float(p[3] or 0) if p else 0
    pz = int(p[7] or 0) if p else 0
    print(f"{th[:9]:<10}{(str(pl)+'→'+str(r[1]))[:13]:<14}"
          f"{pc:5.0f}→{float(r[4] or 0):<9.0f}{pt:5.0f}→{float(r[3] or 0):<9.0f}"
          f"{pz:4d}→{int(r[7] or 0):<8d}{float(r[6] or 0):7.1f} {float(r[8] or 0):7.1f} "
          f"{str(r[11]):<6}{r[12]:<5}{str(r[2] or '')}")

print()
print('=== 前一日 gate_feat（含 cap_state）===')
for th, r in P.items():
    gf = r[13]
    if gf:
        try:
            d = json.loads(gf)
            print(f"  {th[:10]:<11} tier={r[11]:<5} cap_state={d.get('cap_state',''):<6} days={d.get('days')}")
        except Exception:
            pass
conn.close()
