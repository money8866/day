# -*- coding: utf-8 -*-
"""下蹲信号池（缩量比>=0.50 + 距T0开盘>=5%）：持有窗口 T+5 / T+10 / T+20 的峰值出现日分布"""
import io, sys, sqlite3
from collections import Counter
import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import volume_surge_select as vsw

vsw.VOL_SHRINK_MIN_STEP = 0.0
vsw.VOL_T0_PREM_MIN = 0.0

name_map = {}
try:
    name_map = vsw.load_stock_dict() or {}
except Exception:
    pass

conn = sqlite3.connect(r'd:\mystock\cache_daily\stock_data.db')
df = pd.read_sql("select ts_code,trade_date,open,high,low,close,pre_close,vol "
                 "from daily_cache where trade_date>='20250101' order by ts_code,trade_date", conn)
conn.close()
df = df[~df['ts_code'].str.endswith('.BJ')].reset_index(drop=True)
groups = list(df.groupby('ts_code', sort=False))

ev = []
for code, g in groups:
    g = g.reset_index(drop=True)
    if len(g) < 185:
        continue
    o = g['open'].values.astype(float); h = g['high'].values.astype(float)
    l = g['low'].values.astype(float); c = g['close'].values.astype(float)
    pc = g['pre_close'].values.astype(float); v = g['vol'].values.astype(float)
    dates = g['trade_date'].values
    vs = pd.Series(v)
    vr = vs / np.maximum(vs.rolling(20, min_periods=1).mean().values, 1)
    rmax = vr.rolling(200, min_periods=1).max().values
    gt2 = (vr > 2.0).rolling(200, min_periods=1).sum().values
    mx20 = vr.rolling(20).max().values; mn20 = vr.rolling(20).mean().values
    pct = (c / np.maximum(pc, 0.01) - 1) * 100
    m = ((np.arange(len(c)) >= 179) & (rmax >= 2.6) & (gt2 >= 5) & (mx20 >= 2.0) &
         (mn20 >= 1.4) & (vr.values <= 1.2) & (pct <= 1.0) & (pct > -7.0))
    for i in np.where(m)[0]:
        ds = str(dates[i])
        if not ('20250901' <= ds <= '20260930') or i + 1 >= len(c):
            continue
        try:
            r = vsw.detect_volume_surge_swing(code, code, _df_override=g.iloc[:i + 1].reset_index(drop=True))
        except Exception:
            continue
        if not r or not r.get('下蹲信号'):
            continue
        shr = float(r.get('缩量比') or 0)
        t0o = float(r.get('T0开盘价') or 0)
        if shr < 0.50 or t0o <= 0:
            continue
        prem = (c[i] / t0o - 1) * 100
        if prem < 5.0:
            continue
        buy = o[i + 1]
        if buy <= 0:
            continue
        ev.append({'date': ds, 'code': code, 'name': name_map.get(code, ''),
                   'i': i, 'buy': buy, 'h': h})

OUT = open(r'd:\mystock\solo\_tmp_w.txt', 'w', encoding='utf-8')


def w(s):
    print(s)
    OUT.write(s + '\n')
    OUT.flush()


def peak(e, W):
    """返回 (峰值出现于下蹲日后第几天, 峰值幅度%)；数据不足 W 天返回 (None,None)"""
    i, buy, h = e['i'], e['buy'], e['h']
    if i + 1 + W > len(h):
        return None, None
    seg = h[i + 1:i + 1 + W] / buy - 1
    k = int(np.argmax(seg)) + 1
    return k, float(seg[k - 1]) * 100


WINDOWS = (5, 10, 20)

w(f'信号池 n={len(ev)}（缩量比>=0.50 + 距T0开盘>=5%，T+1开盘买，峰值=窗口内最高价/买入价-1）')
w('')

for W in WINDOWS:
    full = [e for e in ev if e['i'] + 1 + W <= len(e['h'])]
    inc = [e for e in ev if e['i'] + 1 + W > len(e['h'])]
    ks = []; gs = []
    for e in full:
        k, g = peak(e, W)
        ks.append(k); gs.append(g)
    cc = Counter(ks)
    w(f'══ T+{W} 持有窗口：完整样本 n={len(full)}'
      + (f'（未走满 {len(inc)}：' + '、'.join(f"{e['date']}/{e['code']}" for e in inc) + '）' if inc else ''))
    if W <= 10:
        for d in range(1, W + 1):
            cnt = cc.get(d, 0)
            w(f'  d+{d:<2}: {cnt:>2} 只 ({cnt / len(full) * 100:>5.1f}%)')
    else:
        for a, b in [(1, 2), (3, 5), (6, 10), (11, 15), (16, 20)]:
            cnt = sum(cc.get(d, 0) for d in range(a, b + 1))
            w(f'  d+{a}~{b:<2}: {cnt:>2} 只 ({cnt / len(full) * 100:>5.1f}%)')
        w('  逐日：' + ' '.join(f'd{d}:{cc.get(d, 0)}' for d in range(1, W + 1)))
    w(f'  中位数 d+{int(np.median(ks))} | 峰值均值 {np.mean(gs):+.2f}% | 峰值中位数 {np.median(gs):+.2f}%')
    w('')

common = [e for e in ev if e['i'] + 1 + 20 <= len(e['h'])]
w(f'══ 共同样本（T+20 走满）n={len(common)} —— 三窗口横向对比')
w('  下蹲日/代码/名称              T+5峰值    T+10峰值   T+20峰值 | 出现日 5/10/20')
for e in sorted(common, key=lambda x: x['date']):
    res = [peak(e, W) for W in WINDOWS]
    w(f"  {e['date']} {e['name']}({e['code']})"
      f"  {res[0][1]:>+7.2f}%  {res[1][1]:>+7.2f}%  {res[2][1]:>+7.2f}%"
      f" | d+{res[0][0]}/d+{res[1][0]}/d+{res[2][0]}")
w('')
for lbl, fn in (('均值', np.mean), ('中位', np.median)):
    vals = [fn([peak(e, W)[1] for e in common]) for W in WINDOWS]
    w(f'  共同样本峰值{lbl}：T+5 {vals[0]:+.2f}% | T+10 {vals[1]:+.2f}% | T+20 {vals[2]:+.2f}%')
OUT.close()
