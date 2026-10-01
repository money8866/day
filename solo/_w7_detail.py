# -*- coding: utf-8 -*-
import sqlite3, json
con = sqlite3.connect('file:d:/mystock/solo/picks_db/stock_picks.db?mode=ro', uri=True)
con.row_factory = sqlite3.Row

print('=== w7_hvt 全部明细（按日） ===')
rows = con.execute("""
 select p.pick_date, p.ts_code, p.stock_name, p.industry, p.close, p.pct_chg,
        p.score, p.rank_no, p.signal, p.action, p.reason, p.stop_price, p.target_price,
        p.indicators,
        t.ret_1d, t.ret_3d, t.ret_5d, t.ret_10d, t.max_gain, t.max_drawdown, t.hit_date, t.last_date
   from stock_pick p left join pick_tracking t
     on t.strategy_id=p.strategy_id and t.ts_code=p.ts_code and t.pick_date=p.pick_date
  where p.strategy_id='w7_hvt' and p.pick_date>='20260916' order by p.pick_date, p.score desc
""").fetchall()

def g(v, w=6, p=2):
    return ('%*.2f' % (w, v)) if v is not None else (' ' * w)

cur = None
for x in rows:
    if x['pick_date'] != cur:
        cur = x['pick_date']
        print()
        print('---- %s ----' % cur)
    print('%s %-8s %-6s %-8s 收%8.2f 涨%6.2f 分%5.1f r%2s %-14s %-10s | 1d%s 3d%s 5d%s 10d%s | max+%6.2f max-%.2f'
          % (x['ts_code'], (x['stock_name'] or '')[:6], (x['industry'] or '')[:8],
             '', x['close'] or 0, x['pct_chg'] or 0, x['score'] or 0, x['rank_no'],
             (x['signal'] or '')[:14], (x['action'] or '')[:10],
             g(x['ret_1d']), g(x['ret_3d']), g(x['ret_5d']), g(x['ret_10d']),
             x['max_gain'] or 0, x['max_drawdown'] or 0))
    if x['reason']:
        print('        reason: %s' % x['reason'])

print()
print('=== 一条样本的 indicators 结构 ===')
r = con.execute("select indicators from stock_pick where strategy_id='w7_hvt' order by pick_date desc limit 1").fetchone()
print(r['indicators'])
con.close()
