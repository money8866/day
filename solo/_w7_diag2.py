# -*- coding: utf-8 -*-
import sqlite3
cc = sqlite3.connect('file:D:/mystock/cache_daily/stock_data.db?mode=ro', uri=True)
print('daily_cache trade_date range:', cc.execute(
    "select min(trade_date), max(trade_date), count(distinct trade_date) from daily_cache").fetchone())
pc = sqlite3.connect('file:d:/mystock/solo/picks_db/stock_picks.db?mode=ro', uri=True)
rows = pc.execute("""select p.pick_date, p.ts_code, t.ret_3d from stock_pick p
   left join pick_tracking t on t.pick_date=p.pick_date and t.strategy_id=p.strategy_id
   and t.ts_code=p.ts_code where p.strategy_id='w7_hvt' order by p.pick_date""").fetchall()
print('w7_hvt total', len(rows))
bad_no_track, bad_no_bars, ok = 0, 0, 0
per = {}
for d, code, r3 in rows:
    n = cc.execute("select count(*) from daily_cache where ts_code=? and trade_date<=?", (code, d)).fetchone()[0]
    tag = 'ok'
    if r3 is None:
        tag = 'NO_R3(无跟踪行)'
        bad_no_track += 1
    elif n < 25:
        tag = 'NO_BARS(%d)' % n
        bad_no_bars += 1
    else:
        ok += 1
    per.setdefault(d, []).append(tag)
print('ok=%d  no_r3=%d  no_bars=%d' % (ok, bad_no_track, bad_no_bars))
print()
for d in sorted(per):
    from collections import Counter
    c = Counter(per[d])
    print('  %s n=%d  %s' % (d, len(per[d]), dict(c)))
cc.close(); pc.close()
