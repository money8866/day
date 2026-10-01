# -*- coding: utf-8 -*-
"""W7 归因终检：什么因子预测超额收益？两段是否反转？"""
import sqlite3
cc = sqlite3.connect('file:D:/mystock/cache_daily/stock_data.db?mode=ro', uri=True)
days = [r[0] for r in cc.execute("select distinct trade_date from daily_cache where trade_date between "
                                 "'20260601' and '20261015' order by trade_date")]
mi = {d: i for i, d in enumerate(days)}
mkt = {d: (a, u) for d, a, u in cc.execute(
    """select trade_date, avg(pct_chg), sum(case when pct_chg>0 then 1 else 0 end)*100.0/count(*)
       from daily_cache where trade_date>='20260601' group by trade_date""")}

def mkt_feat(d):
    if d not in mi:
        return None
    i = mi[d]
    if i < 5:
        return None
    return {
        'mkt_5d': sum(mkt[days[j]][0] for j in range(i - 4, i + 1)),      # 信号日前5日等权累计
        'mkt_10d': sum(mkt[days[j]][0] for j in range(i - 9, i + 1)),
        'mkt_up5': sum(mkt[days[j]][1] for j in range(i - 4, i + 1)) / 5,  # 前5日均上涨占比
        'mkt_1d': mkt[days[i]][0],
        'mkt_up1': mkt[days[i]][1],
    }

def fwd_mkt(d, n):
    if d not in mi:
        return None
    s = 0.0
    for k in range(1, n + 1):
        j = mi[d] + k
        if j >= len(days):
            return None
        s += mkt[days[j]][0]
    return s

def stock_feat(code, date):
    bars = cc.execute("select trade_date, high, low, close, vol from daily_cache where ts_code=? and "
                      "trade_date<=? order by trade_date desc limit 61", (code, date)).fetchall()
    if len(bars) < 25:
        return None
    bars = bars[::-1]
    cl = [b[3] for b in bars]
    hi = [b[1] for b in bars]
    vo = [b[4] for b in bars]
    i = len(bars) - 1
    c = cl[i]
    m5 = sum(cl[i - 4:i + 1]) / 5
    m10 = sum(cl[i - 9:i + 1]) / 10
    m20 = sum(cl[i - 19:i + 1]) / 20
    h20 = max(hi[i - 19:i + 1])
    l20 = min(hi[i - 19:i + 1])
    return {'off20': (c / h20 - 1) * 100,
            'd20': (c / m20 - 1) * 100,
            'r5': (c / cl[i - 5] - 1) * 100,
            'r10': (c / cl[i - 10] - 1) * 100,
            'r20': (c / cl[i - 20] - 1) * 100,
            'volr': vo[i] / (sum(vo[i - 19:i + 1]) / 20),
            'pos20': (c - l20) / (h20 - l20) * 100 if h20 > l20 else 50}

pc = sqlite3.connect('file:d:/mystock/solo/picks_db/stock_picks.db?mode=ro', uri=True)
sig = pc.execute("""select p.pick_date, p.ts_code, p.stock_name, p.score, t.ret_3d, t.max_gain
                    from stock_pick p left join pick_tracking t on t.pick_date=p.pick_date
                    and t.strategy_id=p.strategy_id and t.ts_code=p.ts_code
                    where p.strategy_id='w7_hvt' order by p.pick_date""").fetchall()

recs = []
for d, code, nm, score, r3, mg in sig:
    if r3 is None:
        continue
    fm = fwd_mkt(d, 3)
    mf = mkt_feat(d)
    sf = stock_feat(code, d)
    if fm is None or mf is None or sf is None:
        continue
    r = {'d': d, 'code': code, 'nm': nm, 'exc3': r3 - fm, 'r3': r3, 'mg': mg}
    r.update(mf)
    r.update(sf)
    recs.append(r)
print('样本 n =', len(recs))

A = [r for r in recs if r['d'] <= '20260916']
B = [r for r in recs if r['d'] >= '20260917']
print('前期 n=%d  转差 n=%d' % (len(A), len(B)))

FAC = ['volr', 'ige_proxy', 'off20', 'd20', 'r5', 'r10', 'r20', 'pos20',
       'mkt_5d', 'mkt_up5', 'mkt_1d', 'mkt_up1']
print()
print('【特征均值：前期 vs 转差】')
print('%-10s %10s %10s' % ('特征', '前期', '转差'))
for f in FAC:
    if f == 'ige_proxy':
        continue
    va = sum(r[f] for r in A) / len(A)
    vb = sum(r[f] for r in B) / len(B)
    print('%-10s %+10.2f %+10.2f' % (f, va, vb))

print()
print('【特征 → 3日超额的二分档表现（按全样本中位数切）】')
print('%-10s %6s | %-28s | %-28s' % ('特征', '中位', '前期：低档 / 高档', '转差：低档 / 高档'))
for f in FAC:
    if f == 'ige_proxy':
        continue
    vals = sorted(r[f] for r in recs)
    med = vals[len(vals) // 2]
    out = []
    for GRP in (A, B):
        lo = [r['exc3'] for r in GRP if r[f] <= med]
        hi = [r['exc3'] for r in GRP if r[f] > med]
        s = []
        for v in (lo, hi):
            if v:
                w = sum(1 for x in v if x > 0) * 100.0 / len(v)
                s.append('%+6.2f%%(胜%3.0f%% n=%2d)' % (sum(v) / len(v), w, len(v)))
            else:
                s.append('%19s' % '-')
        out.append(' / '.join(s))
    print('%-10s %+6.2f | %-28s | %-28s' % (f, med, out[0], out[1]))

print()
print('【信号日市场状态 → 后期（信号次日及以后）】连续口径')
for lbl, lo, hi in (('次日市场等权上涨占比', 0, 40), (None, 40, 70), (None, 70, 101)):
    for GRP, nm in ((A, '前期'), (B, '转差')):
        v = [r['exc3'] for r in GRP if lo <= (r['mkt_up1'] if False else r['mkt_up5']) < hi]
        if v:
            print('  前5日均上涨占比 [%d,%d)  %s n=%2d 平均超额3日 %+6.2f%%' % (lo, hi, nm, len(v), sum(v) / len(v)))
cc.close()
pc.close()
