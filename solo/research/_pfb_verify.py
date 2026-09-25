# -*- coding: utf-8 -*-
"""Post-FirstBoard Alpha V2.0 · 参数扰动（§38-⑨ / STOP-2）+ 构造稳健性 + OOS 内部分割

不是调参：本脚本只回答「C3 是否只是一个极窄参数点」
  * 邻域网格：OVEREXT 低分位阈值 pO ∈ {.15,.20,.25,.30,.35,.40,.45,.50}
             RS 高分位阈值      pR ∈ {.40,.50,.60,.70}
  * 构造替换：复合 → 单一代表变量（dma20_2 / rs_cum12）
  * 时间分割：OOS 内按季度、ALL 内按年
"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
ROOT = os.path.dirname(HERE)
M = dict(np.load(os.path.join(OUT, 'tr_mats.npz'), allow_pickle=False))

d0 = pd.read_parquet(os.path.join(OUT, 'pfb_feat.parquet'))
d0['_row'] = np.arange(len(d0))
d = d0[d0['clean'] & (d0['listed_days'] >= 60) & d0['can_buy']].reset_index(drop=True)
comp = pd.read_parquet(os.path.join(OUT, 'pfb_comp.parquet'))
d = d.merge(comp.drop(columns=['period', 'mon']), on='event_id', how='left')
d['mon'] = (d['t0_date'] // 100).astype(int)
d['qtr'] = (d['t0_date'] // 100).astype(str).str[:4] + 'Q' + \
           ((d['trade_date'] % 10000 // 100 - 1) // 3 + 1).astype(str)
d['ym'] = d['t0_date'] // 100
COST = 0.00255

rk = {c: d.groupby('mon')[c].rank(pct=True) for c in ('F_OVEREXT', 'F_RS', 'dma20_2', 'rs_cum12')}
PER = ['ALL', 'TRAIN', 'VALID', 'OOS']
rows = []


def st(mask, cost=COST):
    v = d.loc[mask, 'fwd5'].values.astype(float)
    v = v[np.isfinite(v)] - cost
    if v.size == 0:
        return dict(N=0, med=np.nan, mean=np.nan, win=np.nan, pf=np.nan)
    up = v[v > 0].sum(); dn = -v[v < 0].sum()
    return dict(N=int(v.size), med=float(np.median(v)), mean=float(v.mean()),
                win=float((v > 0).mean()), pf=float(up / dn) if dn > 1e-12 else np.inf)


print('=== §38-⑨ 参数邻域网格（T+5 中位数 %, 含 0.255% 成本）===')
print('\nNE = F_OVEREXT 低分位阈值 / NR = F_RS 高分位阈值')
grid = {}
for pO in (0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50):
    for pR in (0.40, 0.50, 0.60, 0.70):
        m = (rk['F_OVEREXT'] <= pO) & (rk['F_RS'] >= 1 - pR)
        m = np.asarray(m.fillna(False))
        r = {'pO': pO, 'pR': pR}
        for P in PER:
            s = m if P == 'ALL' else (m & (d['period'].values == P))
            z = st(s)
            r['N_' + P] = z['N']; r['med_' + P] = z['med']; r['win_' + P] = z['win']
        rows.append(r)
        grid[(pO, pR)] = r
G = pd.DataFrame(rows)
for k in ('med', 'win'):
    for P in ('ALL', 'TRAIN', 'VALID', 'OOS'):
        t = G.pivot_table(index='pO', columns='pR', values='%s_%s' % (k, P))
        print('\n-- %s_%s' % (k, P))
        print((t * (100 if k == 'med' else 1)).round(3).to_string())
t = G.pivot_table(index='pO', columns='pR', values='N_ALL')
print('\n-- N_ALL')
print(t.astype(int).to_string())
print('\n-- 符号一致性：各 (pO,pR) 下 TRAIN/VALID/OOS 三项同为正的比例')
trip = G[(G.med_TRAIN > 0) & (G.med_VALID > 0) & (G.med_OOS > 0)]
print('%d / %d 个参数点三期同正' % (len(trip), len(G)))
G.to_csv(os.path.join(OUT, 'pfb_grid.csv'), index=False)

print('\n\n=== 构造稳健性：复合 vs 单一代表变量 ===')
VAR = {
    'C3_复合(OVEREXT低40%+RS高40%)': (rk['F_OVEREXT'] <= .4) & (rk['F_RS'] >= .6),
    'C3a_单一(dma20_2低40%+rs_cum12高40%)': (rk['dma20_2'] <= .4) & (rk['rs_cum12'] >= .6),
    'C3b_单一(dma20_2低40%+RS高40%)': (rk['dma20_2'] <= .4) & (rk['F_RS'] >= .6),
    'C3c_复合(OVEREXT低40%+rs_cum12高40%)': (rk['F_OVEREXT'] <= .4) & (rk['rs_cum12'] >= .6),
    'C3d_仅OVEREXT低40%(无RS)': (rk['F_OVEREXT'] <= .4),
    'C3e_仅RS高40%(无OVEREXT)': (rk['F_RS'] >= .6),
}
rr = []
for name, m in VAR.items():
    m = np.asarray(pd.Series(m).fillna(False))
    r = {'variant': name}
    for P in PER:
        s = m if P == 'ALL' else (m & (d['period'].values == P))
        z = st(s)
        for k in ('N', 'med', 'mean', 'win', 'pf'):
            r['%s_%s' % (k, P)] = z[k]
    rr.append(r)
V = pd.DataFrame(rr)
print(V[['variant', 'N_ALL', 'med_ALL', 'mean_ALL', 'win_ALL', 'pf_ALL']].round(4).to_string(index=False))
print('\n-- 分期 T+5 中位数 %')
print(V.set_index('variant')[['med_TRAIN', 'med_VALID', 'med_OOS']].mul(100).round(3).to_string())
print('\n-- 分期 N')
print(V.set_index('variant')[['N_TRAIN', 'N_VALID', 'N_OOS']].astype(int).to_string())
V.to_csv(os.path.join(OUT, 'pfb_variant.csv'), index=False)

print('\n\n=== 时间分割 ===')
base = np.asarray(((rk['F_OVEREXT'] <= .4) & (rk['F_RS'] >= .6)).fillna(False))
print('\n-- C3 按年（T+5 中位数 %, 含成本）')
yr = d['yr'].values
for y in sorted(set(yr)):
    m = base & (yr == y)
    z = st(m)
    zb = st(d['period'].values == 'X')  # placeholder
    print('%d  N=%4d  med=%7.3f%%  win=%.3f  pf=%.2f' %
          (y, z['N'], z['med'] * 100, z['win'], z['pf']))
print('\n-- C3 按季度（仅 2024 起）')
qt = d['qtr'].values
for q in sorted(set(qt)):
    if q[:4] < '2024':
        continue
    m = base & (qt == q)
    z = st(m)
    print('%s  N=%4d  med=%7.3f%%  win=%.3f' % (q, z['N'], z['med'] * 100, z['win']))
print('\n-- C3 月度中位数分布（ALL）')
mo = sorted(set(d['mon'].values))
mv = []
for mth in mo:
    m = base & (d['mon'].values == mth)
    z = st(m)
    if z['N'] >= 3:
        mv.append(z['med'])
mv = np.array(mv)
print('月份数=%d  正比例=%.3f  中位=%.3f%%  P25=%.3f%%  P75=%.3f%%' %
      (len(mv), (mv > 0).mean(), np.median(mv) * 100,
       np.percentile(mv, 25) * 100, np.percentile(mv, 75) * 100))

print('\n\n=== §18 第二基准：E1(T+1收盘信号→T+2开盘) vs E2(T+2收盘信号→T+3开盘) ===')
for tag, col in (('E1_fwd5_e1', 'fwd5_e1'), ('E2_fwd5', 'fwd5')):
    for P in PER:
        s = d if P == 'ALL' else d[d['period'] == P]
        v = s[col].values.astype(float); v = v[np.isfinite(v)] - COST
        print('%-12s %-6s N=%5d  中位=%7.3f%%  均值=%7.3f%%  胜率=%.3f  PF=%.2f' %
              (tag, P, len(v), np.median(v) * 100, v.mean() * 100,
               (v > 0).mean(), v[v > 0].sum() / max(1e-12, -v[v < 0].sum())))
print('\n-- C3 在 E1 口径下')
for P in PER:
    s = d if P == 'ALL' else d[d['period'] == P]
    m = base & (d['period'].values == P) if P != 'ALL' else base
    v = s.loc[m if P == 'ALL' else m[s.index], 'fwd5_e1'].values.astype(float)
    v = v[np.isfinite(v)] - COST
    print('C3_E1 %-6s N=%5d  中位=%7.3f%%  胜率=%.3f' %
          (P, len(v), np.median(v) * 100, (v > 0).mean()))
