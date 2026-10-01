# -*- coding: utf-8 -*-
"""环境闸门阈值敏感性"""
import sqlite3
cc = sqlite3.connect('file:D:/mystock/cache_daily/stock_data.db?mode=ro', uri=True)
days = [r[0] for r in cc.execute("select distinct trade_date from daily_cache where trade_date between "
                                 "'20260601' and '20261015' order by trade_date")]
mi = {d: i for i, d in enumerate(days)}
mkt = {d: (a, u) for d, a, u in cc.execute(
    """select trade_date, avg(pct_chg), sum(case when pct_chg>0 then 1 else 0 end)*100.0/count(*)
       from daily_cache where trade_date>='20260601' group by trade_date""")}

def fwd(d, n):
    s = 0.0
    for k in range(1, n + 1):
        j = mi[d] + k
        if j >= len(days):
            return None
        s += mkt[days[j]][0]
    return s

pc = sqlite3.connect('file:d:/mystock/solo/picks_db/stock_picks.db?mode=ro', uri=True)
rows = pc.execute("""select p.pick_date, p.ts_code, t.ret_3d from stock_pick p left join pick_tracking t
   on t.pick_date=p.pick_date and t.strategy_id=p.strategy_id and t.ts_code=p.ts_code
   where p.strategy_id='w7_hvt' order by p.pick_date""").fetchall()
R = []
for d, code, r3 in rows:
    f = fwd(d, 3)
    if f is None or r3 is None:
        continue
    i = mi[d]
    R.append({'d': d, 'code': code, 'r3': r3, 'exc3': r3 - f,
              'm5': sum(mkt[days[j]][0] for j in range(i - 4, i + 1)),
              'm5p': sum(mkt[days[j]][0] for j in range(i - 5, i)),       # 不含当日
              'up5': sum(mkt[days[j]][1] for j in range(i - 4, i + 1)) / 5,
              'up1': mkt[d][1]})
A = [r for r in R if r['d'] <= '20260916']
B = [r for r in R if r['d'] >= '20260917']

def show(name, key, cuts):
    print('%-8s %-22s %-30s %-30s %-30s' % ('指标', '保留条件', '前期(保留n/均超额)', '转差(保留n/均超额)', '全样本(保留n/均超额)'))
    for c in cuts:
        cells = []
        for G in (A, B, R):
            v = [r['exc3'] for r in G if r[key] < c]
            cells.append('%3d / %+6.2f%%' % (len(v), sum(v) / len(v)) if v else '  0 /      -')
        print('%-8s %-22s %-30s %-30s %-30s' % (name, '< %+.2f' % c, cells[0], cells[1], cells[2]))
    cells = []
    for G in (A, B, R):
        v = [r['exc3'] for r in G]
        cells.append('%3d / %+6.2f%%' % (len(v), sum(v) / len(v)))
    print('%-8s %-22s %-30s %-30s %-30s' % (name, '不过滤(现状)', cells[0], cells[1], cells[2]))
    print()

print('样本 前期 n=%d  转差 n=%d' % (len(A), len(B)))
print()
show('市场5日', 'm5', [-2.0, -0.5, 0.5, 1.5, 2.5, 3.5])
show('市场5日', 'm5p', [-2.0, -0.5, 0.5, 1.5, 2.5, 3.5])
show('市场5日', 'up5', [35, 42, 48, 52, 58])
show('信号日', 'up1', [35, 45, 55, 65])
cc.close(); pc.close()
