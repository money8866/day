# -*- coding: utf-8 -*-
"""Post-FirstBoard Alpha V2.0 · 复合因子 / 交互项 / 增量信息（§14/§15/§16/§17/§18）

原则
  * 方向全部由 TRAIN 决定，VALIDATION/OOS 只做检验（不反向调参）
  * 分层仍在「月内截面」完成，跨月聚合
  * 复合因子只由「经济含义明确」的同族变量等权合成，不做权重搜索

输出
  research/post_firstboard_alpha_interactions_YYYYMMDD.csv
  out/pfb_comp.parquet（复合因子与分组标签，供后续脚本复用）
"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
ROOT = os.path.dirname(HERE)
DATE = '20260925'
M = dict(np.load(os.path.join(OUT, 'tr_mats.npz'), allow_pickle=False))


def c(m, k):
    return m[:, k].astype(np.float64)


d0 = pd.read_parquet(os.path.join(OUT, 'pfb_feat.parquet'))
d0['_row'] = np.arange(len(d0))
d = d0[d0['clean'] & (d0['listed_days'] >= 60) & d0['can_buy']].reset_index(drop=True)
d['mon'] = (d['t0_date'] // 100).astype(int)
N = len(d)
ri = d['_row'].values


def ck(m, k):
    return m[ri, k].astype(np.float64)


# 额外锚点收益（§29 时间锚点反事实）
for tag, ke in (('e2', 2), ('e4', 4)):
    d['fwd5_%s' % tag] = ck(M['cl'], ke + 5 - 1) / ck(M['op'], ke) - 1
d['fwd5_sig'] = ck(M['cl'], 7) / ck(M['cl'], 2) - 1   # T+2 收盘进场（不可交易参照）
d['fwd5_t0'] = ck(M['cl'], 5) / ck(M['cl'], 0) - 1    # T0 收盘进场（事件锚点参照）

# ── 月内 rank 工具 ──
def mrank(col):
    return d.groupby('mon')[col].rank(pct=True)


FAMILY = {
    'OVEREXT': ['dma20_2', 'dma60_2', 'ma10slope_2', 'ma20slope_2', 'distC0_2'],
    'VOLHEAT': ['vr2', 'vo_vov0_2', 'rng2', 'rvol20_2', 'amp1'],
    'TOHEAT': ['to2', 'to_vr2', 'to_ma3_2'],
    'WEAKCLOSE': ['cp2', 'cp1', 'distL0_2'],
    'RS': ['rs_cum12', 'rs_ind_cum12'],
}
R = {}
for f in FAMILY:
    rk = [mrank(x) for x in FAMILY[f]]
    R[f] = pd.concat(rk, axis=1).mean(axis=1)
    d[f'F_{f}'] = R[f]
d['F_OVEREXT_x_VOL'] = R['OVEREXT'] + R['VOLHEAT']
d['F_OVEREXT_x_TO'] = R['OVEREXT'] + R['TOHEAT']

# ── 单因子（TRAIN 内 direction）与复合因子 五分位表 ──
def qtab(fac, horizon='fwd5', periods=('ALL', 'TRAIN', 'VALID', 'OOS'), q=5, tag=''):
    out = []
    r = d.groupby('mon')[fac].rank(pct=True)
    dd = d[r.notna()].copy()
    dd['_q'] = pd.qcut(r[dd.index].rank(method='first'), q, labels=False) + 1
    for P in periods:
        s = dd if P == 'ALL' else dd[dd['period'] == P]
        g = s.groupby('_q')[horizon]
        for k in range(1, q + 1):
            v = g.get_group(k) if k in g.groups else pd.Series(dtype=float)
            out.append({'factor': tag or fac, 'period': P, 'horizon': horizon,
                        'q': 'Q%d' % k, 'N': len(v),
                        'med': float(v.median()) if len(v) else np.nan,
                        'mean': float(v.mean()) if len(v) else np.nan,
                        'win': float((v > 0).mean()) if len(v) else np.nan,
                        'p25': float(v.quantile(.25)) if len(v) else np.nan,
                        'p75': float(v.quantile(.75)) if len(v) else np.nan})
    return out


rows = []
FCOLS = ['F_' + k for k in FAMILY] + ['F_OVEREXT_x_VOL', 'F_OVEREXT_x_TO']
for f in FCOLS:
    for h in ('fwd3', 'fwd5', 'fwd10'):
        rows += qtab(f, h)
rows += qtab('F_OVEREXT', 'fwd5', q=10, tag='F_OVEREXT_d10')
QT = pd.DataFrame(rows)
print('=== 复合因子五分位（T+5, 中位数 %）===')
for f in FCOLS:
    t = QT[(QT.factor == f) & (QT.horizon == 'fwd5')]
    print('\n--', f)
    print(t.pivot_table(index='q', columns='period', values='med')
          .reindex(columns=['ALL', 'TRAIN', 'VALID', 'OOS']).mul(100).round(3).to_string())
    print(t.pivot_table(index='q', columns='period', values='N')
          .reindex(columns=['ALL', 'TRAIN', 'VALID', 'OOS']).to_string())

# ── 交互项（3x3） ──
PAIRS = [
    ('OVEREXT_x_VOL', 'F_OVEREXT', 'F_VOLHEAT'),
    ('OVEREXT_x_TO', 'F_OVEREXT', 'F_TOHEAT'),
    ('OVEREXT_x_RS', 'F_OVEREXT', 'F_RS'),
    ('VOL_x_CP', 'vr2', 'cp2'),
    ('TO_x_CP', 'to2', 'cp2'),
    ('T0RET_x_T1RET', 't0_ret', 'r1'),
    ('T0VR_x_T1VR', 't0_vr20', 'vr1'),
    ('RS_x_VOL', 'rs_cum12', 'vr2'),
    ('T0Q_x_OVEREXT', 't0_vr20', 'dma20_2'),
]
inter = []
for name, fa, fb in PAIRS:
    ra = d.groupby('mon')[fa].rank(pct=True)
    rb = d.groupby('mon')[fb].rank(pct=True)
    ok = ra.notna() & rb.notna()
    dd = d[ok].copy()
    dd['_a'] = pd.cut(ra[ok], [0, 1 / 3, 2 / 3, 1.0], labels=['A1', 'A2', 'A3']).astype(str)
    dd['_b'] = pd.cut(rb[ok], [0, 1 / 3, 2 / 3, 1.0], labels=['B1', 'B2', 'B3']).astype(str)
    for P in ['ALL', 'TRAIN', 'VALID', 'OOS']:
        s = dd if P == 'ALL' else dd[dd['period'] == P]
        for h in ('fwd3', 'fwd5'):
            g = s.groupby(['_a', '_b'])[h]
            for (ka, kb), v in g:
                inter.append({'section': 'INTER', 'pair': name, 'period': P, 'horizon': h,
                              'cell': '%s|%s' % (ka, kb), 'N': len(v),
                              'med': float(v.median()), 'mean': float(v.mean()),
                              'win': float((v > 0).mean()),
                              'ex_med': float(v.median() - s[h].median())})
IN = pd.DataFrame(inter)
same = IN[(IN.period == 'ALL') & (IN.horizon == 'fwd5')]
print('\n=== 交互项 3x3（ALL, T+5 中位数 %，仅 N>=200）===')
for name, _, _ in PAIRS:
    t = same[same.pair == name]
    if t.empty:
        continue
    piv = t.pivot_table(index='cell', values='med') * 100
    pivn = t.pivot_table(index='cell', values='N')
    print('\n--', name)
    print(pd.concat([piv.round(3), pivn.astype('Int64')], axis=1).to_string())

# ── 增量信息（§16）：逐层加入条件 ──
def pstats(mask, h='fwd5'):
    v = d.loc[mask, h]
    return dict(N=len(v), med=float(v.median()), mean=float(v.mean()),
                win=float((v > 0).mean()))


base = d['period'].notna()
L = {
    'A_全部首板': base,
    'B_非超买(OVEREXT_低40%)': R['OVEREXT'] <= 0.4,
    'C_B+非量能过热(VOLHEAT低40%)': (R['OVEREXT'] <= 0.4) & (R['VOLHEAT'] <= 0.4),
    'D_C+非换手过热(TOHEAT低40%)': (R['OVEREXT'] <= 0.4) & (R['VOLHEAT'] <= 0.4) & (R['TOHEAT'] <= 0.4),
    'E_D+强收盘(cp2高60%)': (R['OVEREXT'] <= 0.4) & (R['VOLHEAT'] <= 0.4) & (R['TOHEAT'] <= 0.4) & (R['WEAKCLOSE'] >= 0.6),
    'F_E+相对强(RS高40%)': (R['OVEREXT'] <= 0.4) & (R['VOLHEAT'] <= 0.4) & (R['TOHEAT'] <= 0.4) & (R['WEAKCLOSE'] >= 0.6) & (R['RS'] >= 0.6),
    'Z_反向_超买(OVEREXT高20%)': R['OVEREXT'] >= 0.8,
}
L = {k: np.asarray(v) for k, v in L.items()}
print('\n=== §16 增量信息（逐层加入，月内分层）===')
inc_rows = []
for k, m in L.items():
    for P in ['ALL', 'TRAIN', 'VALID', 'OOS']:
        s = m & (d['period'].values == P) if P != 'ALL' else m
        st3 = pstats(s, 'fwd3'); st5 = pstats(s, 'fwd5'); st10 = pstats(s, 'fwd10')
        inc_rows.append({'section': 'INC', 'group': k, 'period': P,
                         'N': st5['N'],
                         't3_med': st3['med'], 't3_win': st3['win'],
                         't5_med': st5['med'], 't5_mean': st5['mean'], 't5_win': st5['win'],
                         't10_med': st10['med']})
INCR = pd.DataFrame(inc_rows)
print(INCR[INCR.period == 'ALL'].drop(columns=['section', 'period']).to_string(index=False))
print('\n-- 分期 T+5 中位数')
print(INCR.pivot_table(index='group', columns='period', values='t5_med')
      .reindex(columns=['TRAIN', 'VALID', 'OOS']).mul(100).round(3).to_string())
print('\n-- 分期样本')
print(INCR.pivot_table(index='group', columns='period', values='N')
      .reindex(columns=['TRAIN', 'VALID', 'OOS']).to_string())

d[['event_id', 'period', 'mon'] + ['F_' + k for k in FAMILY] +
  ['F_OVEREXT_x_VOL', 'F_OVEREXT_x_TO']].to_parquet(
    os.path.join(OUT, 'pfb_comp.parquet'), index=False)
QT.to_csv(os.path.join(OUT, 'pfb_qt_comp.csv'), index=False)
IN.to_csv(os.path.join(ROOT, 'post_firstboard_alpha_interactions_%s.csv' % DATE), index=False)
INCR.to_csv(os.path.join(OUT, 'pfb_incr.csv'), index=False)
print('\n已写 post_firstboard_alpha_interactions / out/pfb_qt_comp.csv / out/pfb_incr.csv')
