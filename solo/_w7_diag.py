# -*- coding: utf-8 -*-
import sqlite3, json
con = sqlite3.connect('file:d:/mystock/solo/picks_db/stock_picks.db?mode=ro', uri=True)
con.row_factory = sqlite3.Row

print('=== w7_hvt 落库总览 ===')
r = con.execute("select count(*) n, min(pick_date) d0, max(pick_date) d1 from stock_pick where strategy_id='w7_hvt'").fetchone()
print('  条数', r['n'], '| 区间', r['d0'], '~', r['d1'])
r = con.execute("select count(*) n from pick_tracking where strategy_id='w7_hvt'").fetchone()
print('  跟踪条数', r['n'])

print()
print('=== 全部交易日精选数（w7_hvt 有落库的日期） ===')
rows = con.execute("""
 select p.pick_date, count(*) n,
        round(avg(t.ret_1d),2) a1,
        round(avg(t.ret_3d),2) a3,
        round(avg(t.ret_5d),2) a5,
        round(avg(t.ret_10d),2) a10,
        sum(case when t.ret_3d>0 then 1 else 0 end) w3n,
        count(t.ret_3d) n3,
        sum(case when t.ret_5d>0 then 1 else 0 end) w5n,
        count(t.ret_5d) n5,
        round(avg(p.score),1) sc,
        max(t.last_date) lastdu
   from stock_pick p left join pick_tracking t
     on t.strategy_id=p.strategy_id and t.ts_code=p.ts_code and t.pick_date=p.pick_date
  where p.strategy_id='w7_hvt'
  group by p.pick_date order by p.pick_date
""").fetchall()
print('日期     只数  均1日  均3日  均5日  均10日  3日胜率  5日胜率  均分  跟踪末日')
for x in rows:
    w3 = '%2d/%2d' % (x['w3n'], x['n3']) if x['n3'] else '  -  '
    w5 = '%2d/%2d' % (x['w5n'], x['n5']) if x['n5'] else '  -  '
    def f(v):
        return ('%6.2f' % v) if v is not None else '     -'
    print('%s %4d %s %s %s %s   %s   %s  %5.1f  %s'
          % (x['pick_date'], x['n'], f(x['a1']), f(x['a3']), f(x['a5']), f(x['a10']), w3, w5, x['sc'], x['lastdu']))
con.close()
