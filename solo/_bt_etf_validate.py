# -*- coding: utf-8 -*-
"""分割验证：确认口径改进不是过拟合"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd

df = pd.read_csv('_bt_out/etf_industry_panel.csv')
df['d'] = df['d'].astype(str)
df = df[df['fwd20'].notna()].copy()
df['label'] = (df['fwd_ex'] > 5).astype(int)
df = df.sort_values('d').reset_index(drop=True)

def score(d, ex20_min, ex60_min, dd_min, above_req, amt_min=None, brd_min=None):
    m = (d.ex20 >= ex20_min) & (d.ex60 >= ex60_min) & (d.dd20 >= dd_min)
    if above_req: m = m & (d.above20 == True)
    if amt_min is not None: m = m & (d.amt_ratio >= amt_min)
    if brd_min is not None: m = m & (d.above20_ratio >= brd_min)
    pos = int(d.label.sum())
    tp = int((m & (d.label == 1)).sum()); n = int(m.sum())
    if n == 0: return 0, 0, 0
    prec = 100*tp/n; rec = 100*tp/max(1,pos)
    return prec, rec, 2*prec*rec/max(1e-9, prec+rec)

# 时间分割：前半段调参、后半段验证
half = df.d.nunique()//2
d1 = df[df.d < df.d.unique()[half]]
d2 = df[df.d >= df.d.unique()[half]]
print(f"训练段 {d1.d.nunique()} 天 ({d1.d.min()}~{d1.d.max()})  "
      f"验证段 {d2.d.nunique()} 天 ({d2.d.min()}~{d2.d.max()})")
print(f"训练段基准正样本率 {100*d1.label.mean():.1f}%  验证段 {100*d2.label.mean():.1f}%\n")

CANDS = [
    ("现状 above20必选 ex20>=2 ex60>=3", (2,3,-999,True,None,None)),
    ("新 ex20>=0 ex60>=5 dd>=-12",        (0,5,-12,False,None,None)),
    ("新 ex20>=1 ex60>=5 dd>=-12",        (1,5,-12,False,None,None)),
    ("新 ex20>=0 ex60>=8 dd>=-12",        (0,8,-12,False,None,None)),
    ("新 ex20>=0 ex60>=5 dd>=-12 amt>=0.9",(0,5,-12,False,0.9,None)),
    ("新 ex20>=0 ex60>=5 dd>=-12 brd>=0.5",(0,5,-12,False,None,0.5)),
]
print(f"{'口径':<40} {'训练F1':>7} {'验证F1':>7} {'验证准确':>8} {'验证召回':>8}")
for nm, args in CANDS:
    p1,r1,f1_ = score(d1, *args)
    p2,r2,f2_ = score(d2, *args)
    print(f"{nm:<40} {f1_:>7.1f} {f2_:>7.1f} {p2:>7.0f}% {r2:>7.0f}%")

print("\n=== 月度 F1（确认改进不是集中在某几个月）===")
print(f"{'口径':<40} " + " ".join(f"{m:>6}" for m in sorted(df.d.str[:6].unique())))
for nm, args in CANDS[:4]:
    row=[]
    for ym in sorted(df.d.str[:6].unique()):
        dd = df[df.d.str[:6]==ym]
        if len(dd)==0: row.append("  -"); continue
        row.append(f"{score(dd,*args)[2]:>6.1f}")
    print(f"{nm:<40} " + " ".join(row))

print("\n=== 参数稳定性：dd20 阈值在验证段的 F1 ===")
for thr in (-8,-10,-12,-15,-18):
    p,r,f = score(d2, 0, 5, thr, False)
    print(f"  dd20>={thr}: 验证段 准确{p:.0f}% 召回{r:.0f}% F1{f:.1f}")
print("\n=== ex60 阈值在验证段的 F1 ===")
for thr in (3,5,6,8,10):
    p,r,f = score(d2, 0, thr, -12, False)
    print(f"  ex60>={thr}: 验证段 准确{p:.0f}% 召回{r:.0f}% F1{f:.1f}")
