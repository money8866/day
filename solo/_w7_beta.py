# -*- coding: utf-8 -*-
"""W7 beta 回归：把 -10pp 的落差拆成 beta 与 alpha"""
import sqlite3
cc = sqlite3.connect('file:D:/mystock/cache_daily/stock_data.db?mode=ro', uri=True)
days = [r[0] for r in cc.execute("select distinct trade_date from daily_cache where trade_date between "
                                 "'20260601' and '20261015' order by trade_date")]
mi = {d: i for i, d in enumerate(days)}
mkt = {d: a for d, a in cc.execute("select trade_date, avg(pct_chg) from daily_cache "
                                   "where trade_date>='20260601' group by trade_date")}

def fwd(d, n):
    if d not in mi:
        return None
    s = 0.0
    for k in range(1, n + 1):
        j = mi[d] + k
        if j >= len(days):
            return None
        s += mkt[days[j]]
    return s

pc = sqlite3.connect('file:d:/mystock/solo/picks_db/stock_picks.db?mode=ro', uri=True)
rows = pc.execute("""select p.pick_date, p.ts_code, t.ret_1d, t.ret_3d, t.ret_5d
   from stock_pick p left join pick_tracking t on t.pick_date=p.pick_date
   and t.strategy_id=p.strategy_id and t.ts_code=p.ts_code
   where p.strategy_id='w7_hvt' order by p.pick_date""").fetchall()

def ols(xs, ys):
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx if sxx else 0
    a = my - b * mx
    # R^2
    ss = sum((y - my) ** 2 for y in ys)
    sr = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
    return a, b, (1 - sr / ss if ss else 0), n

for H, KEY in ((1, 2), (3, 3), (5, 4)):
    data = []
    for d, code, r1, r3, r5 in rows:
        r = {1: r1, 3: r3, 5: r5}[H]
        if r is None:
            continue
        f = fwd(d, H)
        if f is None:
            continue
        data.append((d, code, r, f))
    seg = {'ALL 全样本': data,
           '前期0825~0916': [x for x in data if x[0] <= '20260916'],
           '转差0917~0924': [x for x in data if '20260917' <= x[0] <= '20260924']}
    print('===== T+%d =====' % H)
    from collections import Counter
    print('  覆盖：', dict(Counter(x[0] for x in data)))
    print('%-16s %4s %8s %8s %8s %8s %9s' % ('区间', 'n', 'W7均', '市场均', 'beta', 'alpha', 'R2'))
    for k, v in seg.items():
        if len(v) < 3:
            continue
        a, b, r2, n = ols([x[3] for x in v], [x[2] for x in v])
        print('%-16s %4d %+7.2f%% %+7.2f%% %8.2f %+7.2f%% %8.2f'
              % (k, n, sum(x[2] for x in v) / n, sum(x[3] for x in v) / n, b, a, r2))
    # 反事实：用前期的 beta 套转差期的市场
    A = seg['前期0825~0916']
    B = seg['转差0917~0924']
    if len(A) >= 3 and len(B) >= 3:
        aA, bA, _, nA = ols([x[3] for x in A], [x[2] for x in A])
        aB, bB, _, nB = ols([x[3] for x in B], [x[2] for x in B])
        mA = sum(x[3] for x in A) / nA
        mB = sum(x[3] for x in B) / nB
        print('   中期落差 %.2fpp = [beta效应 %+.2fpp] + [alpha漂移 %+.2fpp] + [交互 %+.2fpp]'
              % (sum(x[2] for x in B) / nB - sum(x[2] for x in A) / nA,
                 bA * (mB - mA), (aB - aA), (bB - bA) * mB))
    print()
cc.close(); pc.close()
