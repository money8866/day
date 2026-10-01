# -*- coding: utf-8 -*-
"""W7 信号质量：买点有效性 / 市场状态耦合"""
import sqlite3, json
pcon = sqlite3.connect('file:d:/mystock/solo/picks_db/stock_picks.db?mode=ro', uri=True)
pcon.row_factory = sqlite3.Row
cc = sqlite3.connect('file:D:/mystock/cache_daily/stock_data.db?mode=ro', uri=True)

mkt = {}
for d, n, avg, up in cc.execute("""select trade_date, count(*), avg(pct_chg),
        sum(case when pct_chg>0 then 1 else 0 end) from daily_cache
        where trade_date between '20260801' and '20261001' group by trade_date order by trade_date"""):
    mkt[d] = (avg, up / n * 100)
mdate = sorted(mkt)

rows = pcon.execute("""select p.pick_date, p.ts_code, p.stock_name, p.industry, p.score,
        p.indicators, t.ret_1d, t.ret_3d, t.ret_5d, t.max_gain, t.max_drawdown
   from stock_pick p left join pick_tracking t
     on t.strategy_id=p.strategy_id and t.ts_code=p.ts_code and t.pick_date=p.pick_date
  where p.strategy_id='w7_hvt' order by p.pick_date""").fetchall()

print('【买点有效性：信号日之后是否给出过正收益空间 max_gain】')
for lbl, lo, hi in (('前期 0825~0916', '0', '20260916'), ('转差期 0917~0924', '20260917', '20260924')):
    sel = [r for r in rows if lo <= r['pick_date'] <= hi and r['max_gain'] is not None]
    if not sel:
        continue
    bad = [r for r in sel if r['max_gain'] < 1.0]
    good = [r for r in sel if r['max_gain'] >= 5.0]
    dd = [r['max_drawdown'] for r in sel if r['max_drawdown'] is not None]
    print('  %s  n=%2d | max_gain<1%%(买点当天即套) %2d 条 %4.0f%% | max_gain>=5%% %2d 条 %3.0f%% '
          '| 平均最大回撤 %.2f%%'
          % (lbl, len(sel), len(bad), 100.0 * len(bad) / len(sel), len(good),
             100.0 * len(good) / len(sel), sum(dd) / len(dd) if dd else 0))

print()
print('【信号日市场状态 vs 信号后续3日收益】')
print('  信号日       全市场等权  上涨占比   信号数  信号均3日  次日市场等权')
for d in sorted(set(r['pick_date'] for r in rows)):
    if d >= '20260925':
        continue
    g = [r for r in rows if r['pick_date'] == d and r['ret_3d'] is not None]
    a3 = sum(r['ret_3d'] for r in g) / len(g) if g else None
    me = mkt.get(d, (None, None))
    i = mdate.index(d) if d in mdate else None
    nxt = mkt[mdate[i + 1]][0] if i is not None and i + 1 < len(mdate) else None
    print('  %s   %9s  %7s   %5d   %8s   %9s'
          % (d, ('%+.2f' % me[0]) if me[0] is not None else '-',
             ('%.1f%%' % me[1]) if me[1] is not None else '-', len(g),
             ('%+.2f' % a3) if a3 is not None else '-',
             ('%+.2f' % nxt) if nxt is not None else '-'))
pcon.close()
cc.close()
