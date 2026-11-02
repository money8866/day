# -*- coding: utf-8 -*-
"""主线判定参数网格搜索 + 半导体行情覆盖专项检验"""
import os, sys, itertools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd

df = pd.read_csv('_bt_out/etf_industry_panel.csv')
df['d'] = df['d'].astype(str)
df = df[df['fwd20'].notna()].copy()
df['label'] = (df['fwd_ex'] > 5).astype(int)
POS = int(df['label'].sum())
BASE = 100*POS/len(df)
print(f"基准正样本率 {BASE:.1f}%  ({POS}/{len(df)})\n")

def score(ex20_min, ex60_min, dd_min, amt_min, above_req, brd_min, use_brd=True):
    m = (df.ex20 >= ex20_min) & (df.ex60 >= ex60_min) & (df.dd20 >= dd_min)
    if above_req: m = m & (df.above20 == True)
    if amt_min is not None: m = m & (df.amt_ratio >= amt_min)
    if use_brd: m = m & (df.above20_ratio >= brd_min)
    tp = int((m & (df.label == 1)).sum()); n = int(m.sum())
    if n == 0: return None
    prec = 100*tp/n; rec = 100*tp/POS
    return prec, rec, 2*prec*rec/max(1e-9, prec+rec), n

print("=== 网格搜索（above20 改为可选，dd20 容忍回撤）===")
print(f"{'ex20':>5} {'ex60':>5} {'dd20':>6} {'amt':>5} {'brd':>5} {'above':>6} | {'准确':>5} {'召回':>5} {'F1':>5} {'命中':>5}")
best = []
for ex20 in (0, 1, 2, 3):
    for ex60 in (3, 5, 8, 10):
        for dd in (-8, -12, -15):
            for amt in (None, 0.9, 1.15):
                for above in (True, False):
                    for brd in (0.0, 0.5):
                        if above is False and brd > 0: continue
                        r = score(ex20, ex60, dd, amt, above, brd)
                        if r is None: continue
                        best.append((r[2], ex20, ex60, dd, amt, above, brd, r))
best.sort(reverse=True)
for f1, ex20, ex60, dd, amt, above, brd, r in best[:18]:
    print(f"{ex20:>5} {ex60:>5} {dd:>6} {str(amt):>5} {brd:>5} {str(above):>6} | "
          f"{r[0]:>5.0f} {r[1]:>5.0f} {r[2]:>5.1f} {r[3]:>5}")

print("\n=== 现状 vs 候选（关注召回+准确平衡）===")
for nm, args in [
    ("现状 above20必选 ex20>=2 ex60>=3", (2, 3, -999, None, True, 0.0)),
    ("候选C ex60>=10 dd>=-12 above可选", (0, 10, -12, None, False, 0.0)),
    ("候选D ex20>=2 ex60>=5 dd>=-12", (2, 5, -12, None, False, 0.0)),
    ("候选E ex20>=1 ex60>=8 dd>=-12 brd>=0.5", (1, 8, -12, None, False, 0.5)),
]:
    r = score(*args)
    if r: print(f"  {nm:40s} 准确{r[0]:>5.0f}% 召回{r[1]:>5.0f}% F1{r[2]:>5.1f} 命中{r[3]}")

# ---------- 半导体行情覆盖专项 ----------
print("\n" + "="*78)
print("半导体行情覆盖专项（2026-04-02 低点 → 2026-06-30 高点）")
print("="*78)
win = df[df.d.between('20260401','20260630')]
semi = win[win.ind.isin(['电子','通信','计算机'])].copy()
print(f"窗口 {win.d.nunique()} 个交易日，电子/通信/计算机 共 {len(semi)} 行")

def cover(ex20_min, ex60_min, dd_min, above_req):
    m = (semi.ex20 >= ex20_min) & (semi.ex60 >= ex60_min) & (semi.dd20 >= dd_min)
    if above_req: m = m & (semi.above20 == True)
    hit_days = semi[m].d.nunique()
    # 电子行业单独看
    el = semi[semi.ind == '电子']
    el_hit = el[m.reindex(el.index, fill_value=False)].d.nunique()
    el_tot = el.d.nunique()
    return hit_days, el_hit, el_tot

for nm, (a,b,c,d) in [
    ("现状 (ex20>=2,ex60>=3,above20必选)", (2,3,-999,True)),
    ("候选 (ex20>=0,ex60>=10,dd>=-12,above可选)", (0,10,-12,False)),
    ("候选 (ex20>=2,ex60>=5,dd>=-12,above可选)", (2,5,-12,False)),
]:
    h, eh, et = cover(a,b,c,d)
    print(f"  {nm:46s} 全行业命中{h:>3}天  电子命中{eh:>3}/{et}天 ({100*eh/max(1,et):.0f}%)")

print("\n=== 电子行业逐日：现状 vs 候选(电子行业专属判定) ===")
el = df[df.ind == '电子'].copy()
el = el[el.d.between('20260315','20260715')]
cur = (el.ex20>=2)&(el.above20==True)&(el.ex60>=3)
new = (el.ex20>=0)&(el.ex60>=10)&(el.dd20>=-12)
print(f"{'日期':<10} {'ex20':>7} {'ex60':>7} {'dd20':>7} {'above20':>8} {'现状':>6} {'候选':>6}")
for _,r in el.iterrows():
    c = '✓' if cur.loc[r.name] else '·'
    n = '✓' if new.loc[r.name] else '·'
    ab = 'True' if r.above20 else 'False'
    print(f"{r.d:<10} {r.ex20:>7.1f} {r.ex60:>7.1f} {r.dd20:>7.1f} {ab:>8} {c:>6} {n:>6}")
