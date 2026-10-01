# -*- coding: utf-8 -*-
"""W7 逐信号明细 + 重复推送谱系"""
import sqlite3, json
db = r'd:\mystock\solo\picks_db\stock_picks.db'
c = sqlite3.connect('file:%s?mode=ro' % db.replace('\\', '/'), uri=True)
rows = c.execute("""
select p.pick_date, p.ts_code, p.stock_name, p.industry, p.score, p.signal, p.action,
       p.indicators, p.stop_price, p.close,
       t.ret_1d, t.ret_3d, t.ret_5d, t.ret_10d, t.max_gain, t.max_drawdown, t.status
from stock_pick p left join pick_tracking t
  on t.pick_date=p.pick_date and t.strategy_id=p.strategy_id and t.ts_code=p.ts_code
where p.strategy_id='w7_hvt' and p.pick_date>='20260910'
order by p.pick_date, p.score desc
""").fetchall()
cnt = {}
for r in c.execute("select ts_code, count(*) from stock_pick where strategy_id='w7_hvt' group by ts_code"):
    cnt[r[0]] = r[1]

def g(js, k):
    try:
        return json.loads(js or '{}').get(k)
    except Exception:
        return None

def f(v, w=6):
    return ('%*.2f' % (w, v)) if isinstance(v, (int, float)) else ('%*s' % (w, '-'))

print('=== 逐信号明细 20260910~ ===')
print('%-9s %-10s %-7s %-8s %5s %5s %-5s %-4s %6s %6s %5s %3s | %6s %6s %6s %6s %6s %6s'
      % ('日期','代码','名称','行业','分','volr','state','type','entry','ige','t120','x次',
         'r1d','r3d','r5d','r10d','maxG','maxDD'))
for r in rows:
    (d, code, nm, ind_, score, sig, act, js, stop, close,
     r1, r3, r5, r10, mg, md, st) = r
    print('%-9s %-10s %-7s %-8s %5.1f %5.3f %-5s %-4s %6.1f %6.1f %5.1f %3d | %s %s %s %s %s %s'
          % (d, code, (nm or '')[:7], (ind_ or '')[:8], score or 0, g(js,'volr') or 0,
             g(js,'state') or '', g(js,'type') or '', g(js,'entry') or 0, g(js,'ige_adj') or 0,
             g(js,'t120') or 0, cnt.get(code, 0),
             f(r1), f(r3), f(r5), f(r10), f(mg), f(md)))

print()
print('=== 重复推送 TOP（同一股票被 w7_hvt 推送次数）===')
for code, n in sorted(cnt.items(), key=lambda x: -x[1])[:12]:
    if n < 2:
        break
    nm = c.execute("select stock_name from stock_pick where strategy_id='w7_hvt' and ts_code=? limit 1",
                   (code,)).fetchone()
    ds = [x[0] for x in c.execute("select pick_date from stock_pick where strategy_id='w7_hvt' "
                                  "and ts_code=? order by pick_date", (code,))]
    print('  %-10s %-8s x%d  %s' % (code, (nm[0] if nm else ''), n, ','.join(x[4:] for x in ds)))
c.close()
