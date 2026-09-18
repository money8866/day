# -*- coding: utf-8 -*-
"""回放 theme_scores.db 历史行，量化 L2/L1 各条件的阻塞点（诊断用，可删）"""
import sqlite3, json
from collections import Counter, defaultdict

DB = r'd:/mystock/solo/report_daily/theme_scores.db'
conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute("SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date")
dates = [r[0] for r in cur.fetchall()]
cur.execute("""SELECT trade_date, theme, lifecycle, theme_state, target_state, trend_score,
                      composite_score, migration_score, up_ratio, zt_count, fund_acc,
                      hot_percentile, gate_cap_amt, gate_tier, days_strong
               FROM theme_scores ORDER BY trade_date, theme""")
cols = ['dt','theme','lc','tstate','tgt','trend','comp','mig','up','zt','fund',
        'hotp','cap','tier','days']
rows = [dict(zip(cols, r)) for r in cur.fetchall()]
conn.close()

by_day = defaultdict(dict)
for r in rows:
    by_day[r['dt']][r['theme']] = r

f = lambda v: float(v or 0)
i = lambda v: int(v or 0)

STRONG = lambda lc: lc in ('主升', '升温')
stat = Counter()
blk = Counter()          # L2 条件阻塞统计（仅 lc==主升 的行）
l1blk = Counter()
day_hits = defaultdict(list)  # dt -> ['L2:theme']
lc_dist = Counter()
db_days_dist = Counter()

for idx, dt in enumerate(dates):
    day = by_day[dt]
    prev_day = by_day[dates[idx-1]] if idx > 0 else {}
    for th, r in day.items():
        lc = str(r['lc'] or '')
        p = prev_day.get(th) or {}
        plc = str(p.get('lc') or '')
        pstate = str(p.get('tstate') or '')
        strong_today = lc in ('主升', '升温') or ('分歧转一致' in str(r['tgt'] or '') and lc == '分歧')
        strong_prev = STRONG(plc) or STRONG(pstate)
        ds = int(strong_today) + int(strong_prev)
        db_days_dist[int(r['days'] or 0)] += 1
        lc_dist[lc] += 1
        overheat = f(r['hotp']) >= 85
        q_base = (i(r['zt']) >= 4 or f(r['trend']) >= 70) and f(r['cap']) >= 8 and not overheat
        if lc == '主升':
            stat['主升行'] += 1
            if not (f(r['comp']) >= 68): blk['综合<68'] += 1
            if not (f(r['trend']) >= 68): blk['趋势<68'] += 1
            if not (ds >= 2): blk['持续性<2日'] += 1
            if not q_base: blk['q_base失败'] += 1
            if not (f(r['up']) >= 55): blk['宽度<55'] += 1
            if not (f(r['fund']) >= 40): blk['资金<40'] += 1
            if (f(r['comp']) >= 68 and f(r['trend']) >= 68 and ds >= 2 and q_base
                    and f(r['up']) >= 55 and f(r['fund']) >= 40):
                stat['L2达标'] += 1
                day_hits[dt].append('L2:' + th)
        if lc in ('升温', '分歧'):
            if (('分歧转一致' in str(r['tgt'] or '')) or lc == '升温') and q_base and ds >= 2 \
                    and f(r['mig']) >= 8 and f(r['up']) >= 55:
                stat['L1达标'] += 1
                day_hits[dt].append('L1:' + th)

print('=== 交易日数 ===', len(dates), dates[0], '~', dates[-1])
print('=== 落库 days_strong 分布 ===', dict(db_days_dist))
print('=== 生命周期分布 ===', dict(lc_dist))
print('=== L2 阻塞点（分母=主升行 %d）===' % stat['主升行'], dict(blk))
print('=== L1 阻塞点 ===', dict(l1blk))
print('=== 达标 ===', dict(stat))
print('=== 达标明细 ===')
for dt in sorted(day_hits):
    print(' ', dt, day_hits[dt])
