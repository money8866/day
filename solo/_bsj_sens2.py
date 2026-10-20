# -*- coding: utf-8 -*-
"""临时探针（修正版）：按生产口径（20250101 起）重做 vol_activity 门槛敏感性
并输出 ①博实结在各门槛下的入池与信号 ②T0距今 vs vol_activity 的衰减关系 ③全市场影响面
"""
import sys, sqlite3
import numpy as np
import pandas as pd

sys.path.insert(0, r'd:\mystock\solo')
import volume_surge_select as V

DB = r'd:\mystock\cache_daily\stock_data.db'
con = sqlite3.connect(DB)
codes = [r[0] for r in con.execute("SELECT DISTINCT ts_code FROM daily_cache WHERE trade_date='20261019'")]
rows = con.execute("SELECT ts_code, trade_date, open, high, low, close, vol, amount, pct_chg, pre_close "
                    "FROM daily_cache WHERE ts_code IN (%s) AND trade_date>='20250101' "
                    "ORDER BY ts_code, trade_date" % ','.join('?' * len(codes)), codes).fetchall()
con.close()
cols = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol', 'amount', 'pct_chg', 'pre_close']
full = pd.DataFrame(rows, columns=cols)
full['trade_date'] = full['trade_date'].astype(str)
groups = {c: g.reset_index(drop=True) for c, g in full.groupby('ts_code')}
cand = {c: g for c, g in groups.items() if len(g) >= 180}
print(f"生产口径(>=20250101) 可测股票 = {len(cand)} / 全市场 {len(codes)}")

def run_with(thr):
    old = V.VOL_ACTIVITY_MIN
    V.VOL_ACTIVITY_MIN = thr
    n = sq = 0
    bsj = None
    for c, g in cand.items():
        try:
            r = V.detect_volume_surge_swing(c, c, _df_override=g)
        except Exception:
            r = None
        if r is None:
            continue
        n += 1
        if r.get('下蹲信号'):
            sq += 1
        if c == '301608.SZ':
            bsj = r
    V.VOL_ACTIVITY_MIN = old
    return n, sq, bsj

print("\n========== 门槛敏感性（生产口径，20261019）==========")
for thr in (3.0, 2.5, 2.0):
    n, sq, bsj = run_with(thr)
    print(f"  VOL_ACTIVITY_MIN={thr:<4} 入池={n:>4}   其中下蹲信号={sq:>3}")
    if bsj:
        print(f"      ✅ 博实结入池：信号={bsj.get('信号')} 量能活跃度={bsj.get('量能活跃度')} "
              f"T0={bsj.get('T0日期')} 下蹲信号={bsj.get('下蹲信号')} 下蹲等级={bsj.get('下蹲等级')}")
        for k in ['距MA5', '距MA20', '缩量比', '距T0开盘', '评分', '日均振幅', 'T0涨幅', '量比']:
            if k in bsj:
                print(f"        {k} = {bsj[k]}")
    else:
        print(f"      ❌ 博实结未入池")

# ---- T0距今 vs vol_activity 衰减 ----
print("\n========== vol_activity 随 T0距今的衰减（生产口径）==========")
recs = []
for c, g in cand.items():
    try:
        v = g['vol'].values.astype(float)[-200:]
        cl = g['close'].values.astype(float)[-200:]
        op = g['open'].values.astype(float)[-200:]
        pc = g['pre_close'].values.astype(float)[-200:]
        if len(v) < 180:
            continue
        zt = 19.5 if c.split('.')[0].startswith(('300', '301', '688')) else 9.8
        st = V._vol_step_days(v, cl, op, pc, zt)
        if st is None:
            st = V._vol_step_days(v, cl, op, pc, zt, allow_zt=True)
        if st is None:
            continue
        t0i = len(v) - 1 - st[0]
        if t0i < 21:
            continue
        base = float(np.mean(v[t0i-20:t0i]))
        base_ex = float(np.mean(v[t0i-20:t0i-1]))
        recs.append(dict(code=c, t0i=t0i, dist=len(v)-1-t0i, base=base, base_ex=base_ex,
                         surge=float(np.mean(v[t0i:])), act=float(np.mean(v[t0i:]))/max(base, 1e-9),
                         act_ex=float(np.mean(v[t0i:]))/max(base_ex, 1e-9),
                         contam=bool(v[t0i-1] > base * 1.5)))
    except Exception:
        continue

d = pd.DataFrame(recs)
print(f"  有 T0 的股票 = {len(d)}")
bins = [(0, 3), (4, 7), (8, 14), (15, 25), (26, 60)]
print(f"  {'T0距今':<10} {'n':>5} {'act均(现用)':>12} {'act均(剔污染)':>14} {'现用过3.0占比':>14} {'剔污染过3.0':>13}")
for lo, hi in bins:
    s = d[(d['dist'] >= lo) & (d['dist'] <= hi)]
    if not len(s):
        continue
    a3 = (s['act'] >= 3.0).mean() * 100
    a3x = (s['act_ex'] >= 3.0).mean() * 100
    print(f"  {lo}~{hi:<8} {len(s):>5} {s['act'].mean():>12.2f} {s['act_ex'].mean():>14.2f} {a3:>13.0f}% {a3x:>12.0f}%")

contam = d[d['contam']]
if len(contam):
    print(f"\n  T0前一日为巨量(>基线1.5x)的票 = {len(contam)} ({len(contam)/len(d)*100:.0f}%)")
    print(f"    剔除污染后 act 均值 {contam['act_ex'].mean():.2f}（现用 {contam['act'].mean():.2f}）")
    print(f"    过3.0数量：现用 {int((contam['act']>=3.0).sum())} → 剔污染 {int((contam['act_ex']>=3.0).sum())}  (净增 {int((contam['act_ex']>=3.0).sum()-(contam['act']>=3.0).sum())})")
