# -*- coding: utf-8 -*-
"""终检：池子随市场抬升而漂移？加环境闸门能救回多少？"""
import sqlite3
cc = sqlite3.connect('file:D:/mystock/cache_daily/stock_data.db?mode=ro', uri=True)
days = [r[0] for r in cc.execute("select distinct trade_date from daily_cache where trade_date between "
                                 "'20260601' and '20261015' order by trade_date")]
mi = {d: i for i, d in enumerate(days)}
mkt = {d: (a, u) for d, a, u in cc.execute(
    """select trade_date, avg(pct_chg), sum(case when pct_chg>0 then 1 else 0 end)*100.0/count(*)
       from daily_cache where trade_date>='20260601' group by trade_date""")}

def fwd(d, n):
    if d not in mi:
        return None
    s = 0.0
    for k in range(1, n + 1):
        j = mi[d] + k
        if j >= len(days):
            return None
        s += mkt[days[j]][0]
    return s

def mf(d):
    i = mi[d]
    return {'mkt_5d': sum(mkt[days[j]][0] for j in range(i - 4, i + 1)),
            'mkt_up5': sum(mkt[days[j]][1] for j in range(i - 4, i + 1)) / 5,
            'mkt_up1': mkt[d][1]}

def sf(code, date):
    bars = cc.execute("select high, low, close, vol from daily_cache where ts_code=? and trade_date<=? "
                      "order by trade_date desc limit 61", (code, date)).fetchall()[::-1]
    cl = [b[2] for b in bars]; hi = [b[0] for b in bars]; vo = [b[3] for b in bars]
    i = len(bars) - 1; c = cl[i]
    h20 = max(hi[i - 19:i + 1])
    return {'off20': (c / h20 - 1) * 100, 'r5': (c / cl[i - 5] - 1) * 100,
            'volr': vo[i] / (sum(vo[i - 19:i + 1]) / 20)}

pc = sqlite3.connect('file:d:/mystock/solo/picks_db/stock_picks.db?mode=ro', uri=True)
rows = pc.execute("""select p.pick_date, p.ts_code, p.stock_name, t.ret_3d, t.max_gain
   from stock_pick p left join pick_tracking t on t.pick_date=p.pick_date
   and t.strategy_id=p.strategy_id and t.ts_code=p.ts_code
   where p.strategy_id='w7_hvt' order by p.pick_date""").fetchall()
R = []
for d, code, nm, r3, mg in rows:
    f = fwd(d, 3)
    if f is None or r3 is None:
        continue
    x = {'d': d, 'code': code, 'nm': nm, 'r3': r3, 'exc3': r3 - f, 'mg': mg}
    x.update(mf(d)); x.update(sf(code, d))
    R.append(x)
print('n =', len(R))

def corr(a, b):
    n = len(a); ma = sum(a) / n; mb = sum(b) / n
    sa = sum((x - ma) ** 2 for x in a) ** .5; sb = sum((x - mb) ** 2 for x in b) ** .5
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb) if sa and sb else 0

print()
print('【池子漂移机制】全样本相关性')
for x, y in (('mkt_5d', 'off20'), ('mkt_up5', 'off20'), ('mkt_5d', 'r5'),
             ('mkt_5d', 'volr'), ('mkt_5d', 'exc3'), ('off20', 'exc3')):
    print('  corr(%-8s, %-6s) = %+.3f' % (x, y, corr([r[x] for r in R], [r[y] for r in R])))
print('  前期 corr(mkt_5d, off20) = %+.3f' % corr([r['mkt_5d'] for r in R if r['d'] <= '20260916'],
                                                 [r['off20'] for r in R if r['d'] <= '20260916']))
print('  转差 corr(mkt_5d, off20) = %+.3f' % corr([r['mkt_5d'] for r in R if r['d'] >= '20260917'],
                                                 [r['off20'] for r in R if r['d'] >= '20260917']))

print()
print('【环境闸门反事实：按信号日市场状态过滤，看两段结果】')
print('%-34s %12s %12s %12s' % ('闸门规则', '前期 n/均超额', '转差 n/均超额', '两段合计 n/均超额'))
gates = [
    ('无闸门（现状）', lambda r: True),
    ('信号日市场上涨占比 < 55%', lambda r: r['mkt_up1'] < 55),
    ('信号日市场上涨占比 < 45%', lambda r: r['mkt_up1'] < 45),
    ('前5日市场等权 < +1.5%', lambda r: r['mkt_5d'] < 1.5),
    ('前5日市场等权 < 0%', lambda r: r['mkt_5d'] < 0),
    ('前5日均上涨占比 < 48%', lambda r: r['mkt_up5'] < 48),
    ('前5日等权<+1.5% 且 上涨占比<55%', lambda r: r['mkt_5d'] < 1.5 and r['mkt_up1'] < 55),
    ('前5日等权<0% 且 上涨占比<50%', lambda r: r['mkt_5d'] < 0 and r['mkt_up1'] < 50),
]
for nm, g in gates:
    cells = []
    for lbl, cond in (('A', lambda r: r['d'] <= '20260916'), ('B', lambda r: r['d'] >= '20260917'), ('T', lambda r: True)):
        v = [r['exc3'] for r in R if cond(r) and g(r)]
        cells.append('%4d / %+7.2f%%' % (len(v), sum(v) / len(v)) if v else '   0 /       -')
    print('%-34s %14s %14s %14s' % (nm, cells[0], cells[1], cells[2]))

print()
print('【口径改为绝对收益（含beta）】')
for nm, g in gates:
    cells = []
    for cond in (lambda r: r['d'] <= '20260916', lambda r: r['d'] >= '20260917', lambda r: True):
        v = [r['r3'] for r in R if cond(r) and g(r)]
        cells.append('%4d / %+7.2f%%' % (len(v), sum(v) / len(v)) if v else '   0 /       -')
    print('%-34s %14s %14s %14s' % (nm, cells[0], cells[1], cells[2]))
cc.close(); pc.close()
