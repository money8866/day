# -*- coding: utf-8 -*-
"""买点有效性验证 + 同标的冷却反事实"""
import sqlite3
pc = sqlite3.connect('file:d:/mystock/solo/picks_db/stock_picks.db?mode=ro', uri=True)
rows = pc.execute("""select p.pick_date, p.ts_code, p.stock_name, t.ret_1d, t.ret_3d, t.max_gain,
                     t.max_drawdown, t.status
   from stock_pick p left join pick_tracking t on t.pick_date=p.pick_date
   and t.strategy_id=p.strategy_id and t.ts_code=p.ts_code
   where p.strategy_id='w7_hvt' order by p.pick_date""").fetchall()

def rep(lbl, sel):
    if not sel:
        return
    n = len(sel)
    mg = [r[5] for r in sel if r[5] is not None]
    md = [r[6] for r in sel if r[6] is not None]
    r3 = [r[4] for r in sel if r[4] is not None]
    bad = sum(1 for x in mg if x < 1)
    good = sum(1 for x in mg if x >= 5)
    win = sum(1 for x in r3 if x > 0)
    print('  %-22s n=%3d | maxG<1%% %2d/%2d=%4.0f%% | maxG>=5%% %2d/%2d=%4.0f%% | 均maxG %+6.2f%% | 均maxDD %5.2f%% | 3日胜率 %3.0f%%'
          % (lbl, n, bad, len(mg), bad * 100.0 / len(mg) if mg else 0,
             good, len(mg), good * 100.0 / len(mg) if mg else 0,
             sum(mg) / len(mg) if mg else 0, sum(md) / len(md) if md else 0,
             win * 100.0 / len(r3) if r3 else 0))

print('【买点有效性：分段】')
A = [r for r in rows if r[0] <= '20260916']
B = [r for r in rows if '20260917' <= r[0] <= '20260924']
C = [r for r in rows if r[0] >= '20260928']
rep('前期 0825~0916', A); rep('转差 0917~0924', B); rep('最新 0928~0930', C)

print()
print('【同标的冷却反事实：每只股票 N 个交易日内只取首次信号】')
cc = sqlite3.connect('file:D:/mystock/cache_daily/stock_data.db?mode=ro', uri=True)
TDAYS = [r[0] for r in cc.execute("select distinct trade_date from daily_cache where trade_date "
                                  "between '20260801' and '20261015' order by trade_date")]
cc.close()
TI = {d: i for i, d in enumerate(TDAYS)}
for COOL in (5, 10, 20):
    seen, kept, drop = {}, [], []
    for r in rows:
        d, code = r[0], r[1]
        last = seen.get(code)
        if last is None or TI[d] - TI[last] >= COOL:
            seen[code] = d
            kept.append(r)
        else:
            drop.append(r)
    print()
    print('  --- 冷却 %d 个交易日 ---' % COOL)
    rep('现状 全部', rows)
    rep('冷却后 保留', kept)
    rep('被冷却掉 剔除', drop)
    A = [r for r in kept if r[0] <= '20260916']
    B = [r for r in kept if r[0] >= '20260917']
    rep('  冷却后 前期', A)
    rep('  冷却后 转差', B)

pc.close()
