# -*- coding: utf-8 -*-
import sqlite3, json, collections
c = sqlite3.connect('file:d:/mystock/solo/picks_db/stock_picks.db?mode=ro', uri=True)
rows = c.execute("""select pick_date, created_at, indicators from stock_pick
                    where strategy_id='w7_hvt' order by pick_date""").fetchall()
print('%-10s %-20s %-20s %3s  %s' % ('pick_date', 'created_at(最早)', 'created_at(最晚)', 'n', 'state 分布'))
by = collections.OrderedDict()
for d, ca, js in rows:
    by.setdefault(d, []).append((ca, js))
for d, lst in by.items():
    cas = sorted(x[0] or '' for x in lst)
    st = collections.Counter()
    for _, js in lst:
        try:
            st[json.loads(js or '{}').get('state')] += 1
        except Exception:
            st['?'] += 1
    print('%-10s %-20s %-20s %3d  %s' % (d, cas[0], cas[-1], len(lst), dict(st)))
c.close()
