# -*- coding: utf-8 -*-
"""诊断: classify_v3 PRIMARY_BUY(n≈292) 与 实验es>=70&trio(n=724) 的缺口来源"""
import os, sys, json
import numpy as np
import pandas as pd

BASE = r"d:\mystock\solo"
sys.path.insert(0, BASE)
from hvt_bull.backtest import _tail_stat

CSV = os.path.join(BASE, "report_daily", "hvt_bull_backtest_events_20250101_20260828.csv")
df = pd.read_csv(CSV)
m = df['breakout_realtime'].astype(str).eq('True')
m &= df['breakout'].astype(str).eq('True')
m &= ~df['false_breakout'].astype(str).eq('True')
x = df[m].copy()
print("base:", len(x))

def parse_subs(series):
    out = pd.DataFrame(index=series.index)
    for key in ('扩张空间','压缩结构','动量加速','RS加速','量效',
                '供给吸收','基本面加速','催化剂'):
        vals = []
        for v in series:
            try:
                d = json.loads(v) if isinstance(v, str) and v.strip() else {}
                vals.append(float(d.get(key, 0) or 0))
            except Exception:
                vals.append(0.0)
        out[key] = vals
    return out

subs = parse_subs(x['exp_subs'])
for c in subs.columns:
    x[c] = subs[c]
x['supply'] = x['供给吸收']
x['vgrade'] = x['volume_grade'].fillna('')
x['rs20'] = pd.to_numeric(x['rs20'], errors='coerce')
x['es'] = pd.to_numeric(x['entry_score'], errors='coerce')

trio = x['supply'].ge(12) & x['vgrade'].isin(['A','A+']) & x['rs20'].ge(70) & x['es'].ge(70)
print("trio&es>=70:", int(trio.sum()))

# 其中有多少被 hard_veto 拦截?
hv = x['hard_veto'].fillna('').astype(str)
vetoed = trio & (hv != '') & (hv != 'nan')
print("trio&es>=70 中被hard_veto拦截:", int(vetoed.sum()))
print("hard_veto内容分布:")
for v, n in hv[vetoed].value_counts().items():
    print("  %-60s %d" % (str(v)[:60], n))

# 被拦截事件的表现（r_break_20）
st_v = _tail_stat(x[vetoed])
print("被拦截事件(全部trio中):", {k: st_v.get(k) for k in ('n','win','mean','median','p90','ge20','ge30','top10_avg')})

# 未被拦截的（应接近PRIMARY_BUY）
kept = trio & ~vetoed
st_k = _tail_stat(x[kept])
print("未被拦截:", {k: st_k.get(k) for k in ('n','win','mean','median','p90','ge20','ge30','top10_avg')})

# 逐个 veto 原因拆解
print()
print("按veto类型拆解表现:")
hv_lst = hv[vetoed]
st_all_v = _tail_stat(x[vetoed])
for reason in sorted(set(r for sub in hv_lst for r in str(sub).split('|'))):
    mm = vetoed & hv.str.contains(reason, regex=False)
    st = _tail_stat(x[mm])
    if st['n']:
        print("  %-30s n=%d win=%.1f mean=%.2f p90=%.1f ge20=%.1f ge30=%.1f" % (
            reason, st['n'], st['win'], st['mean'], st['p90'], st['ge20'], st['ge30']))

# 与 classify 的 PRIMARY_BUY 对比
pb = x['v3_state'].eq('PRIMARY_BUY')
st_pb = _tail_stat(x[pb])
print()
print("v3_state=PRIMARY_BUY:", {k: st_pb.get(k) for k in ('n','win','mean','median','p90','ge20','ge30','top10_avg')})
print("kept 与 PRIMARY_BUY 集合差:", int(kept.sum()), int(pb.sum()), "交集", int((kept & pb).sum()))
