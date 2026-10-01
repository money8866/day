# -*- coding: utf-8 -*-
"""W7 收益分解：市场beta vs 个股alpha（逐信号口径）"""
import sqlite3
cc = sqlite3.connect('file:D:/mystock/cache_daily/stock_data.db?mode=ro', uri=True)
days = [r[0] for r in cc.execute("select distinct trade_date from daily_cache where trade_date between "
                                 "'20260820' and '20261015' order by trade_date")]
mi = {d: i for i, d in enumerate(days)}
mkt = {d: (a, u) for d, a, u in cc.execute(
    """select trade_date, avg(pct_chg), sum(case when pct_chg>0 then 1 else 0 end)*100.0/count(*)
       from daily_cache where trade_date>='20260820' group by trade_date""")}

def fwd_mkt(d, n):
    if d not in mi:
        return None
    s = 0.0
    for k in range(1, n + 1):
        j = mi[d] + k
        if j >= len(days):
            return None
        s += mkt[days[j]][0]
    return s

def mkt_seg(d, n):
    if d not in mi:
        return None
    vals = []
    for k in range(1, n + 1):
        j = mi[d] + k
        if j >= len(days):
            return None
        vals.append(mkt[days[j]][1])
    return sum(vals) / len(vals)

pc = sqlite3.connect('file:d:/mystock/solo/picks_db/stock_picks.db?mode=ro', uri=True)
rows = pc.execute("""
select p.pick_date, p.ts_code, p.stock_name, p.score, t.ret_1d, t.ret_3d, t.ret_5d, t.max_gain
from stock_pick p left join pick_tracking t
  on t.pick_date=p.pick_date and t.strategy_id=p.strategy_id and t.ts_code=p.ts_code
where p.strategy_id='w7_hvt' order by p.pick_date""").fetchall()

WIN = [('前期 0825~0916', '20260825', '20260916'),
       ('转差 0917~0924', '20260917', '20260924'),
       ('最新 0928~0930', '20260928', '20260930')]
print('%-16s %4s | %8s %8s %8s | %8s %8s %8s' % ('区间', 'n', 'W7 1日', 'W7 3日', 'W7 5日', '市 3日', '超 3日', '超 5日'))
tot = {}
for lbl, a, b in WIN:
    sel = [r for r in rows if a <= r[0] <= b]
    if not sel:
        continue
    n = len(sel)
    r1 = sum(r[4] for r in sel if r[4] is not None)
    n1 = sum(1 for r in sel if r[4] is not None)
    r3 = sum(r[5] for r in sel if r[5] is not None)
    n3 = sum(1 for r in sel if r[5] is not None)
    r5 = sum(r[6] for r in sel if r[6] is not None)
    n5 = sum(1 for r in sel if r[6] is not None)
    m3 = [fwd_mkt(r[0], 3) for r in sel if fwd_mkt(r[0], 3) is not None]
    m5 = [fwd_mkt(r[0], 5) for r in sel if fwd_mkt(r[0], 5) is not None]
    mm3 = sum(m3) / len(m3) if m3 else 0
    mm5 = sum(m5) / len(m5) if m5 else 0
    a3 = r3 / n3 if n3 else 0
    a5 = r5 / n5 if n5 else 0
    tot[lbl] = (n, r1 / n1 if n1 else 0, a3, a5, mm3, mm5, a3 - mm3, a5 - mm5)
    print('%-16s %4d | %+7.2f%% %+7.2f%% %+7.2f%% | %+7.2f%% %+7.2f%% %+7.2f%%'
          % (lbl, n, r1 / n1 if n1 else 0, a3, a5, mm3, a3 - mm3, a5 - mm5))

print()
print('=== 逐信号 3日超额（个股 - 全市场等权 3日）===')
print('%-9s %-10s %-7s %5s | %8s %8s %8s %8s %6s' % ('日期', '代码', '名称', '分', 'W7 3日', '市场3日', '超额', '市场上涨比', 'maxG'))
agg = {}
for r in rows:
    d, code, nm, score, r1, r3, r5, mg = r
    if d < '20260910' or r3 is None:
        continue
    m = fwd_mkt(d, 3)
    if m is None:
        continue
    print('%-9s %-10s %-7s %5.1f | %+8.2f %+8.2f %+8.2f %8.1f%% %6.2f'
          % (d, code, (nm or '')[:7], score or 0, r3, m, r3 - m, mkt_seg(d, 3) or 0, mg if mg is not None else 0))
    agg.setdefault(d, []).append(r3 - m)

print()
print('=== 按信号日聚合：平均超额3日 ===')
for d in sorted(agg):
    v = agg[d]
    print('  %s  n=%2d  平均超额 %+7.2f%%  (市场次日上涨比 %.1f%%)'
          % (d, len(v), sum(v) / len(v), mkt[days[mi[d] + 1]][1] if d in mi and mi[d] + 1 < len(days) else 0))
cc.close()
pc.close()
