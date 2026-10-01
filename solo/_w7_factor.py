# -*- coding: utf-8 -*-
"""W7 因子在 前期 vs 转差期 的一致性问题"""
import sqlite3, json
con = sqlite3.connect('file:d:/mystock/solo/picks_db/stock_picks.db?mode=ro', uri=True)
con.row_factory = sqlite3.Row
rows = con.execute("""
 select p.pick_date, p.ts_code, p.stock_name, p.industry, p.score,
        t.ret_1d, t.ret_3d, t.ret_5d, t.max_gain, t.max_drawdown, p.indicators
   from stock_pick p left join pick_tracking t
     on t.strategy_id=p.strategy_id and t.ts_code=p.ts_code and t.pick_date=p.pick_date
  where p.strategy_id='w7_hvt' order by p.pick_date""").fetchall()
recs = []
for x in rows:
    d = dict(x)
    try:
        d.update({('i_' + k): v for k, v in json.loads(x['indicators'] or '{}').items()})
    except Exception:
        pass
    d['期'] = '前期' if x['pick_date'] <= '20260916' else '转差期'
    recs.append(d)


def stat(sel, f='ret_3d'):
    v = [r[f] for r in sel if r[f] is not None]
    if not v:
        return None
    return len(v), sum(v) / len(v), 100.0 * sum(1 for z in v if z > 0) / len(v)


def show(title, keyfn, buckets):
    print()
    print('【%s】' % title)
    print('  %-14s %-28s %-28s' % ('分档', '前期 0825~0916', '转差期 0917~0924'))
    for b in buckets:
        cells = []
        for per in ('前期', '转差期'):
            sel = [r for r in recs if r['期'] == per and b[1] <= keyfn(r) < b[2]]
            s = stat(sel)
            cells.append('n=%2d 均3日%+6.2f 胜%3.0f%%' % (s[0], s[1], s[2]) if s else 'n= 0                      ')
        print('  %-14s %-28s %-28s' % (b[0], cells[0], cells[1]))


def g(r, k, dflt=0.0):
    v = r.get(k)
    return v if isinstance(v, (int, float)) else dflt


show('量比 volr（V5.2 只留 <0.66）', lambda r: g(r, 'i_volr', 9),
     [('<0.60', -99, 0.60), ('0.60~0.66', 0.60, 0.66), ('>=0.66(已被V5.2剔除)', 0.66, 99)])
show('IGE 行业景气 ige_adj（V5.3 强=75）', lambda r: g(r, 'i_ige_adj', 0),
     [('<60', -99, 60), ('60~75', 60, 75), ('>=75', 75, 999)])
show('T120 尾随强度', lambda r: g(r, 'i_t120', 0),
     [('<60', -99, 60), ('60~70', 60, 70), ('>=70', 70, 999)])
show('type（MID=V5.3首选）', lambda r: 1 if r.get('i_type') == 'MID' else 0,
     [('EXT', -1, 1), ('MID', 1, 2)])
show('评分 score（V5.3 已降级为展示）', lambda r: r['score'] or 0,
     [('<40', -1, 40), ('40~60', 40, 60), ('>=60', 60, 999)])
show('事件级别 level Ln', lambda r: int(str(r.get('i_level', 'L0'))[1:] or 0),
     [('L3', 3, 4), ('L4', 4, 5), ('L5+', 5, 9)])
show('入选排名 rank 段', lambda r: 0, [('全部', -1, 1)])

print()
print('【全样本 79 条：score 与结果的相关性】')
import statistics
s = [(r['score'] or 0, r['ret_3d']) for r in recs if r['ret_3d'] is not None]
n = len(s)
if n > 2:
    mx = sum(a for a, _ in s) / n
    my = sum(b for _, b in s) / n
    cov = sum((a - mx) * (b - my) for a, b in s) / n
    sx = (sum((a - mx) ** 2 for a, _ in s) / n) ** .5
    sy = (sum((b - my) ** 2 for _, b in s) / n) ** .5
    print('  Pearson(score, ret_3d) = %+.3f   n=%d' % (cov / (sx * sy), n))

print()
print('【转差期逐条：量比 / IGE / 事件年龄 / 结果】')
import datetime
for r in recs:
    if r['期'] != '转差期':
        continue
    age = ''
    if r.get('i_event_date'):
        try:
            age = '%d天' % (datetime.datetime.strptime(r['pick_date'], '%Y%m%d')
                            - datetime.datetime.strptime(r['i_event_date'], '%Y%m%d')).days
        except Exception:
            pass
    print('  %s %-9s %-6s %-6s 分%5.1f volr%.3f ige%5.1f t120%5.1f %-5s | 1d%7s 3d%7s max+%6.1f max-%5.1f'
          % (r['pick_date'], r['ts_code'], (r['stock_name'] or '')[:5], (r['industry'] or '')[:6],
             r['score'] or 0, g(r, 'i_volr', 9), g(r, 'i_ige_adj', 0), g(r, 'i_t120', 0), age,
             ('%+.2f' % r['ret_1d']) if r['ret_1d'] is not None else '   -',
             ('%+.2f' % r['ret_3d']) if r['ret_3d'] is not None else '   -',
             r['max_gain'] or 0, r['max_drawdown'] or 0))
con.close()
