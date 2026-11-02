# -*- coding: utf-8 -*-
"""打分制主线判定 vs 全或无制"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd

df = pd.read_csv('_bt_out/etf_industry_panel.csv')
df['d'] = df['d'].astype(str)
df = df[df['fwd20'].notna()].copy()
df['label'] = (df['fwd_ex'] > 5).astype(int)
df = df.sort_values('d').reset_index(drop=True)

def ev(d, mask, name):
    pos = int(d.label.sum())
    tp = int((mask & (d.label == 1)).sum()); n = int(mask.sum())
    if n == 0: print(f"  {name:<44s} 命中 0"); return 0,0,0
    prec = 100*tp/n; rec = 100*tp/max(1,pos)
    f1 = 2*prec*rec/max(1e-9, prec+rec)
    print(f"  {name:<44s} 命中{n:>5}  准确{prec:>3.0f}% 召回{rec:>3.0f}% F1{f1:>5.1f}")
    return prec, rec, f1

def rule_hard(d):
    return (d.ex20>=0)&(d.ex60>=5)&((d.above20==True)|(d.dd20>=-12))

def rule_score(d, thr=3, need_rs=True):
    """打分制：
       相对强度（必过门槛）+ 趋势分 + 量能分 + 宽度分 >= thr"""
    rs_ok = (d.ex20>=0)&(d.ex60>=5)
    s_trend = ((d.above20==True).astype(int) + (d.dd20>=-12).astype(int))
    s_money = (d.amt_ratio>=1.15).astype(int) + (d.amt_ratio>=0.9).astype(int)
    s_brd = (d.above20_ratio>=0.5).astype(int) + (d.above20_ratio>=0.25).astype(int)
    total = s_trend + s_money + s_brd
    return rs_ok & (total>=thr)

def rule_score2(d, thr=2):
    """打分制 v2：趋势分必含 1 分（回撤容忍），其余加分"""
    rs_ok = (d.ex20>=0)&(d.ex60>=5)
    s_trend = ((d.above20==True).astype(int) + (d.dd20>=-12).astype(int))
    s_money = (d.amt_ratio>=1.15).astype(int)
    s_brd = (d.above20_ratio>=0.5).astype(int)
    total = s_trend + s_money + s_brd
    return rs_ok & (s_trend>=1) & (total>=thr)

half = df.d.nunique()//2
d1 = df[df.d < df.d.unique()[half]]; d2 = df[df.d >= df.d.unique()[half]]

print("=== 全样本 ===")
ev(df, rule_hard(df), "硬规则: ex20>=0 & ex60>=5 & (above20|dd>=-12)")
for thr in (2,3,4):
    ev(df, rule_score(df,thr), f"打分制 thr={thr} (趋势2+量能2+宽度2)")
for thr in (2,3):
    ev(df, rule_score2(df,thr), f"打分制v2 thr={thr} (趋势必>=1)")

print("\n=== 验证段（后半段）===")
ev(d2, rule_hard(d2), "硬规则")
for thr in (2,3,4):
    ev(d2, rule_score(d2,thr), f"打分制 thr={thr}")
for thr in (2,3):
    ev(d2, rule_score2(d2,thr), f"打分制v2 thr={thr}")

print("\n=== 验证段：月度 F1 ===")
print(f"{'规则':<44} " + " ".join(f"{m:>6}" for m in sorted(d2.d.str[:6].unique())))
for nm, fn in [("硬规则", rule_hard),
               ("打分制 thr=2", lambda x: rule_score(x,2)),
               ("打分制 thr=3", lambda x: rule_score(x,3)),
               ("打分制v2 thr=2", lambda x: rule_score2(x,2))]:
    row=[]
    for ym in sorted(d2.d.str[:6].unique()):
        dd = d2[d2.d.str[:6]==ym]
        pos=int(dd.label.sum())
        m=fn(dd); tp=int((m&(dd.label==1)).sum()); n=int(m.sum())
        if n==0: row.append("  -"); continue
        p=100*tp/n; r=100*tp/max(1,pos)
        row.append(f"{2*p*r/max(1e-9,p+r):>6.1f}")
    print(f"{nm:<44} " + " ".join(row))

# 半导体行情覆盖
print("\n=== 半导体行情窗口覆盖（电子行业，4/1-6/30）===")
el = df[df.ind=='电子']
win = el[el.d.between('20260401','20260630')]
for nm, fn in [("现状(above20必选)", lambda x:(x.ex20>=2)&(x.above20==True)&(x.ex60>=3)),
               ("硬规则", rule_hard),
               ("打分制 thr=2", lambda x: rule_score(x,2)),
               ("打分制 thr=3", lambda x: rule_score(x,3))]:
    m = fn(win)
    print(f"  {nm:<22s} 电子命中 {m.sum():>2}/{len(win)} 天 ({100*m.sum()/len(win):>3.0f}%)")
