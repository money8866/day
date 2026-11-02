# -*- coding: utf-8 -*-
"""全子集搜索：找跨「3标签 × 2段」都稳定的 S6 判定条件组合

多重比较控制：
  1. 要求 6 格中至少 5 格提升 > 0
  2. 要求最小格提升 > 0（更严）
  3. 通过率区间约束 15%~70%（太低样本不稳，太高无筛选力）
  4. 训练/验证提升差 < 15pp（防过拟合）
  5. 报告最终组合在 3 标签 × 2 段的完整提升矩阵
"""
import os, sys, itertools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import etf_mainline as em

df = pd.read_csv('_bt_out/six_diag_panel.csv'); df['d'] = df.d.astype(str)
idxdf = pd.read_csv('_bt_out/index_trend_panel.csv'); idxdf['d'] = idxdf.d.astype(str)
df = df.merge(idxdf[['d','ratio20','ratio60','r5','r20']], on='d', how='left')
series = em.compute_industry_series(em.load_etf_panel(), em.load_config())
idx_all = sorted(em.load_etf_panel()['trade_date'].unique())
nav = {i: s.set_index('trade_date')['nav'].reindex(idx_all).values for i, s in series.items()}
bench = np.mean(list(nav.values()), axis=0); pos = {d: i for i, d in enumerate(idx_all)}
df['fwd20'] = df.d.map(lambda d: (bench[pos[d]+20]/bench[pos[d]]-1)*100 if pos[d]+20 < len(bench) else np.nan)
df['past20'] = df.d.map(lambda d: (bench[pos[d]]/bench[pos[d]-20]-1)*100 if pos[d]-20 >= 0 else np.nan)
df['fwd_ex'] = df.fwd20 - df.past20

CONDS = [
    ('T1', '指数 r5>=r20(短期转强)',      lambda d: d.r5 >= d.r20),
    ('T2', '指数 ratio20>=ratio60',        lambda d: d.ratio20 >= d.ratio60),
    ('T3', '指数 ratio20>=0.35(水位)',    lambda d: d.ratio20 >= 0.35),
    ('T4', '指数 ratio60>=0.40',           lambda d: d.ratio60 >= 0.40),
    ('B1', '广度 ma5>=ma20(改善)',         lambda d: d.ma5 >= d.ma20),
    ('B2', '广度 ma5>=45(水位)',           lambda d: d.ma5 >= 45),
    ('B3', '广度 ratio>=50',               lambda d: d.ratio >= 50),
    ('B4', '广度 zt>=30',                  lambda d: d.zt >= 30),
    ('B5', '广度 dt<=30',                  lambda d: d.dt <= 30),
    ('M1', '赚钱 dt<=25',                  lambda d: d.dt <= 25),
    ('M2', '赚钱 smean>=0',                lambda d: d.smean >= 0),
    ('M3', '赚钱 sup>=0.5',                lambda d: d.sup >= 0.5),
    ('M4', '赚钱 promo<35(反向)',          lambda d: d.promo < 35),
]
LABELS = [('fwd20>3%', lambda d: d.fwd_bench > 3),
          ('fwd20>0%', lambda d: d.fwd20 > 0),
          ('fwd_ex>0',  lambda d: d.fwd_ex > 0)]

half = len(df)//2
SEGS = [('训练', df.iloc[:half]), ('验证', df.iloc[half:])]

def boosts(d, mask):
    """返回 3 个标签的提升 pp 与通过率"""
    ok = mask.fillna(False) if hasattr(mask, 'fillna') else mask
    out = []
    for ln, lf in LABELS:
        lab = lf(d)
        base = 100*lab.mean()
        n = int(ok.sum())
        if n < 3:
            out.append(None); continue
        prec = 100*int((ok & lab).sum())/n
        out.append(prec - base)
    return out, 100*ok.mean()

best = []
n_c = len(CONDS)
for k in range(1, 7):
    for combo in itertools.combinations(range(n_c), k):
        masks = {}
        for seg, d in SEGS:
            m = None
            for ci in combo:
                cm = CONDS[ci][2](d)
                m = cm if m is None else (m & cm)
            masks[seg] = m
        b_tr, p_tr = boosts(SEGS[0][1], masks['训练'])
        b_va, p_va = boosts(SEGS[1][1], masks['验证'])
        if any(x is None for x in b_tr+b_va): continue
        if not (15 <= p_tr <= 70 and 15 <= p_va <= 70): continue
        allb = b_tr + b_va
        if min(allb) <= 0: continue              # 要求 6 格全部为正
        if max(allb) - min(allb) > 30: continue  # 跨格一致性
        # 训练/验证差
        if abs(np.mean(b_tr) - np.mean(b_va)) > 15: continue
        best.append((min(allb), np.mean(allb), combo, b_tr, b_va, p_tr, p_va))

best.sort(key=lambda x: (-x[1], -x[0]))
print(f"满足「6 格全正 + 通过率 15~70% + 跨格一致性」的组合: {len(best)}\n")
print("=== Top 15（按平均提升排序）===")
for mng, avg, combo, b_tr, b_va, p_tr, p_va in best[:15]:
    names = ' + '.join(CONDS[i][1] for i in combo)
    print(f"\n[{avg:+.1f}pp 平均, 最小 {mng:+.1f}pp]  {names}")
    print(f"   通过率 训练{p_tr:.0f}% 验证{p_va:.0f}%")
    for j,(ln,_) in enumerate(LABELS):
        print(f"   {ln:<10} 训练{b_tr[j]:+6.1f}pp  验证{b_va[j]:+6.1f}pp")
