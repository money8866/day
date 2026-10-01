# -*- coding: utf-8 -*-
"""W7 二波/突破当日买点 —— 前期好 / 近10日差 的归因分析"""
import sqlite3, json
import statistics as st

PICK_DB = 'file:d:/mystock/solo/picks_db/stock_picks.db?mode=ro'
CACHE_DB = 'file:D:/mystock/cache_daily/stock_data.db?mode=ro'

con = sqlite3.connect(PICK_DB, uri=True)
con.row_factory = sqlite3.Row
cc = sqlite3.connect(CACHE_DB, uri=True)

# ---------- 1. 市场环境 ----------
print('=' * 78)
print('一、市场环境（20260825~20260930）')
print('=' * 78)
idx = {r[0]: {} for r in cc.execute("select distinct ts_code from index_daily_cache "
                                   "where ts_code in ('000300.SH','000852.SH','399006.SZ','000688.SH','000001.SH')")}
for code in idx:
    for d, c in cc.execute("select trade_date, pct_chg from index_daily_cache where ts_code=? "
                           "and trade_date between '20260825' and '20260930' order by trade_date", (code,)):
        idx[code][d] = c
breadth = {}
for d, n, avg, up in cc.execute("""
    select trade_date, count(*) n, avg(pct_chg) avgp,
           sum(case when pct_chg>0 then 1 else 0 end) up
      from daily_cache where trade_date between '20260825' and '20260930'
     group by trade_date order by trade_date"""):
    breadth[d] = (n, avg, up / n * 100 if n else 0)

dates = sorted(set(list(idx['000300.SH'].keys()) + list(breadth.keys())))
print('日期     沪深300  中证1000  创业板指  科创50  全市场等权  上涨占比  信号数  次日均1日')
w7by = {}
for r in con.execute("""select p.pick_date, count(*) n, avg(t.ret_1d) a1
                          from stock_pick p left join pick_tracking t
                            on t.strategy_id=p.strategy_id and t.ts_code=p.ts_code and t.pick_date=p.pick_date
                         where p.strategy_id='w7_hvt' group by 1"""):
    w7by[r[0]] = (r[1], r[2])
for d in dates:
    def f(m, k='000300.SH'):
        v = idx.get(k, {}).get(d)
        return '%7.2f' % v if v is not None else '      -'
    b = breadth.get(d)
    n1 = w7by.get(d)
    print('%s %s %s %s %s   %8s  %7s  %6s  %9s' % (
        d, f(0), f(0, '000852.SH'), f(0, '399006.SZ'), f(0, '000688.SH'),
        ('%6.2f' % b[1]) if b else '     -',
        ('%5.1f%%' % b[2]) if b else '    -',
        ('%5d' % n1[0]) if n1 else '    -',
        ('%6.2f' % n1[1]) if n1 and n1[1] is not None else '     -'))

# ---------- 2. 分段收益 vs 基准 ----------
print()
print('=' * 78)
print('二、w7_hvt 分段表现（相对全市场等权）')
print('=' * 78)


def bucket_of(d):
    if d <= '20260916':
        return '前期 0825~0916'
    if d <= '20260924':
        return '转差 0917~0924'
    return '最新 0928~0929'


rows = con.execute("""
 select p.pick_date, p.ts_code, p.stock_name, p.industry, p.score, p.signal, p.indicators, p.reason,
        t.ret_1d, t.ret_3d, t.ret_5d, t.ret_10d, t.max_gain, t.max_drawdown, p.stop_price sp2
   from stock_pick p left join pick_tracking t
     on t.strategy_id=p.strategy_id and t.ts_code=p.ts_code and t.pick_date=p.pick_date
  where p.strategy_id='w7_hvt' order by p.pick_date, p.score desc""").fetchall()

groups = {}
for x in rows:
    groups.setdefault(bucket_of(x['pick_date']), []).append(x)

# 同期市场等权 1 日均值
mkt = {}
for d, n, avg, up in cc.execute("""select trade_date, count(*), avg(pct_chg), 0 from daily_cache
     where trade_date between '20260825' and '20261001' group by trade_date"""):
    mkt[d] = avg
srt = sorted(mkt)


def fwd_mkt(d, k):
    """d 之后第 k 个交易日全市场等权收益"""
    try:
        i = srt.index(d)
    except ValueError:
        return None
    seg = srt[i + 1:i + 1 + k]
    if len(seg) < k:
        return None
    return sum(mkt[s] for s in seg)


print('%-16s %4s %8s %8s %8s %8s | %8s %8s | %8s %8s' % (
    '区间', 'n', '均1日', '均3日', '均5日', '均10日', '市场1日', '市场5日', '超额3日', '超额5日'))
for k in ('前期 0825~0916', '转差 0917~0924', '最新 0928~0929'):
    g = groups.get(k, [])
    if not g:
        continue
  
    def m(f, kk=1):
        v = [r[f] for r in g if r[f] is not None]
        return sum(v) / len(v) if v else None
    a1, a3, a5, a10 = m('ret_1d'), m('ret_3d'), m('ret_5d'), m('ret_10d')
    m1 = [fwd_mkt(r['pick_date'], 1) for r in g]
    m1 = [v for v in m1 if v is not None]
    m5 = [fwd_mkt(r['pick_date'], 5) for r in g]
    m5 = [v for v in m5 if v is not None]
    mm1 = sum(m1) / len(m1) if m1 else None
    mm5 = sum(m5) / len(m5) if m5 else None
    def s(v):
        return '%8.2f' % v if v is not None else '       -'
    print('%-16s %4d %s %s %s %s | %s %s | %s %s' % (
        k, len(g), s(a1), s(a3), s(a5), s(a10), s(mm1), s(mm5),
        s(a3 - mm5) if (a3 is not None and mm5 is not None) else '       -',
        s(a5 - mm5) if (a5 is not None and mm5 is not None) else '       -'))

# ---------- 3. 因子切片 ----------
print()
print('=' * 78)
print('三、信号结构对比（前期 vs 转差+最新）')
print('=' * 78)


def parse(x):
    try:
        return json.loads(x['indicators'] or '{}')
    except Exception:
        return {}


for k in ('前期 0825~0916', '转差 0917~0924', '最新 0928~0929'):
    g = groups.get(k, [])
    if not g:
        continue
    ind = [parse(x) for x in g]
    def avgv(f, src=ind):
        v = [r.get(f) for r in src if isinstance(r.get(f), (int, float))]
        return sum(v) / len(v) if v else None
    types = {}
    levels = {}
    for r in ind:
        types[r.get('type')] = types.get(r.get('type'), 0) + 1
        levels[r.get('level')] = levels.get(r.get('level'), 0) + 1
    inds = {}
    for x in g:
        inds[x['industry']] = inds.get(x['industry'], 0) + 1
    top = sorted(inds.items(), key=lambda z: -z[1])[:6]
    codes = [x['ts_code'] for x in g]
    uniq = len(set(codes))
    print()
    print('【%s】n=%d 只(去重 %d)' % (k, len(g), uniq))
    print('   type:', ', '.join('%s=%d' % (a, b) for a, b in sorted(types.items(), key=lambda z: -z[1])))
    print('   level:', ', '.join('%s=%d' % (a, b) for a, b in sorted(levels.items(), key=lambda z: -z[1])))
    print('   均分 %.1f | 均 ige_adj %.1f | 均 volr %.3f | 均 entry %.1f | 均 t120 %.1f'
          % (avgv('score', [dict(x) for x in g]) or 0, avgv('ige_adj') or 0,
             avgv('volr') or 0, avgv('entry') or 0, avgv('t120') or 0))
    print('   行业 Top: ' + ', '.join('%s×%d' % (a or '?', b) for a, b in top))
    # 重复推送
    from collections import Counter
    c = Counter(codes)
    print('   重复推送: ' + ', '.join('%s×%d' % (a, b) for a, b in c.most_common(5) if b > 1))
    # 事件年龄
    age = []
    for x, r in zip(g, ind):
        if r.get('event_date'):
            try:
                import datetime
                d1 = datetime.datetime.strptime(x['pick_date'], '%Y%m%d')
                d0 = datetime.datetime.strptime(r['event_date'], '%Y%m%d')
                age.append((d1 - d0).days)
            except Exception:
                pass
    if age:
        print('   天量事件距今: 中位 %d 天  最大 %d 天' % (st.median(age), max(age)))
con.close()
cc.close()
