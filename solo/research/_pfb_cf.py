# -*- coding: utf-8 -*-
"""Post-FirstBoard Alpha V2.0 · 反事实 / 锚点审计 / OOS / WalkForward / 成本 / 赢输可区分性

输出
  research/post_firstboard_alpha_counterfactual_YYYYMMDD.csv
  research/post_firstboard_alpha_oos_YYYYMMDD.csv
  research/post_firstboard_alpha_walkforward_YYYYMMDD.csv
"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
ROOT = os.path.dirname(HERE)
DATE = '20260925'
M = dict(np.load(os.path.join(OUT, 'tr_mats.npz'), allow_pickle=False))

d0 = pd.read_parquet(os.path.join(OUT, 'pfb_feat.parquet'))
d0['_row'] = np.arange(len(d0))
d = d0[d0['clean'] & (d0['listed_days'] >= 60) & d0['can_buy']].reset_index(drop=True)
comp = pd.read_parquet(os.path.join(OUT, 'pfb_comp.parquet'))
d = d.merge(comp.drop(columns=['period', 'mon']), on='event_id', how='left')
d['mon'] = (d['t0_date'] // 100).astype(int)
ri = d['_row'].values


def ck(m, k):
    return m[ri, k].astype(np.float64)


# 多锚点收益（T+5 持有）
ANC = {
    'A1_T0收盘进场(事件锚点,不可交易)': (ck(M['cl'], 0), ck(M['cl'], 5)),
    'A2_T2收盘进场(信号锚点,不可交易)': (ck(M['cl'], 2), ck(M['cl'], 7)),
    'A3_T2开盘进场(提前一天)': (ck(M['op'], 2), ck(M['cl'], 6)),
    'A4_T3开盘进场(真实可交易)': (ck(M['op'], 3), ck(M['cl'], 7)),
    'A5_T4开盘进场(延后一天)': (ck(M['op'], 4), ck(M['cl'], 8)),
}
for k, (a, b) in ANC.items():
    d['anc_' + k[:2]] = b / a - 1.0

FEE = 0.00055


def cost_of(slip):
    return 2 * slip + FEE


# ── 候选组定义（方向由 TRAIN 决定；阈值取「远端分位」，不做网格搜索） ──
q = lambda col, p: d.groupby('mon')[col].rank(pct=True) <= p
QH = lambda col, p: d.groupby('mon')[col].rank(pct=True) >= 1 - p
CAND = {
    'C1_OVEREXT低20%': q('F_OVEREXT', 0.2),
    'C2_OVEREXT低40%': q('F_OVEREXT', 0.4),
    'C3_OVEREXT低40%+RS高40%': q('F_OVEREXT', 0.4) & QH('F_RS', 0.4),
    'C4_OVEREXT低20%+RS高20%': q('F_OVEREXT', 0.2) & QH('F_RS', 0.2),
    'C5_OVEREXTxVOL低20%': q('F_OVEREXT_x_VOL', 0.2),
    'C6_dma20_2低20%': q('dma20_2', 0.2),
    'C7_t0_body高20%': QH('t0_body', 0.2),
    'C8_cp2高20%': QH('cp2', 0.2),
    'Z1_OVEREXT高20%(反向对照)': QH('F_OVEREXT', 0.2),
    'Z2_VOLHEAT高20%(反向对照)': QH('F_VOLHEAT', 0.2),
}
CAND = {k: np.asarray(v.fillna(False)) for k, v in CAND.items()}


def st(v, cost=0.0):
    v = np.asarray(v, dtype=float)
    v = v[np.isfinite(v)] - cost
    if v.size == 0:
        return dict(N=0)
    up = v[v > 0].sum(); dn = -v[v < 0].sum()
    return dict(N=int(v.size), med=float(np.median(v)), mean=float(v.mean()),
                win=float((v > 0).mean()), p25=float(np.percentile(v, 25)),
                p75=float(np.percentile(v, 75)),
                pf=float(up / dn) if dn > 1e-12 else np.inf)


HORS = {'T3': 'fwd3', 'T5': 'fwd5', 'T10': 'fwd10'}
STRATA = [('COST', None), ('OOS_分期', None), ('OOS_年度', None)]
rows = []

# 基线
for P in ['ALL', 'TRAIN', 'VALID', 'OOS']:
    s = d if P == 'ALL' else d[d['period'] == P]
    r = {'section': 'BASE', 'group': 'A_全部首板(真实可交易)', 'period': P}
    for hn, hc in HORS.items():
        x = st(s[hc]); r['N'] = x['N']
        r[hn.lower() + '_med'] = x['med']; r[hn.lower() + '_mean'] = x['mean']
        r[hn.lower() + '_win'] = x['win']; r[hn.lower() + '_pf'] = x['pf']
    r['t5_p25'] = st(s['fwd5'])['p25']; r['t5_p75'] = st(s['fwd5'])['p75']
    rows.append(r)

# 候选 × 分期 × 成本
for name, m in CAND.items():
    for P in ['ALL', 'TRAIN', 'VALID', 'OOS']:
        pm = m if P == 'ALL' else (m & (d['period'].values == P))
        for slip in (0.0, 0.001, 0.002, 0.003):
            r = {'section': 'CAND' if slip == 0.001 else 'COST',
                 'group': name, 'period': P, 'slip': slip,
                 'cost_pct': round(cost_of(slip) * 100, 3)}
            for hn, hc in HORS.items():
                x = st(d.loc[pm, hc], cost_of(slip))
                r['N'] = x['N']
                r[hn.lower() + '_med'] = x.get('med'); r[hn.lower() + '_mean'] = x.get('mean')
                r[hn.lower() + '_win'] = x.get('win'); r[hn.lower() + '_pf'] = x.get('pf')
            r['t5_p25'] = st(d.loc[pm, 'fwd5'], cost_of(slip)).get('p25')
            r['t5_p75'] = st(d.loc[pm, 'fwd5'], cost_of(slip)).get('p75')
            rows.append(r)

# 逐年
for name, m in CAND.items():
    for y in sorted(d['yr'].unique()):
        pm = m & (d['yr'].values == y)
        if pm.sum() < 30:
            continue
        x = st(d.loc[pm, 'fwd5'], cost_of(0.001))
        rows.append({'section': 'OOS_年度', 'group': name, 'period': str(y), 'N': x['N'],
                     't5_med': x['med'], 't5_mean': x['mean'], 't5_win': x['win'], 't5_pf': x['pf']})

# ── §28 反事实：匹配对照 ──
CF = []
d['_mv'] = pd.cut(d.groupby('t0_date')['log_mv'].rank(pct=True), [0, 1 / 3, 2 / 3, 1],
                  labels=['S', 'M', 'L']).astype(str)
d['_r0'] = pd.cut(d.groupby('mon')['t0_ret'].rank(pct=True), [0, 1 / 3, 2 / 3, 1],
                  labels=['R1', 'R2', 'R3']).astype(str)
d['_v0'] = pd.cut(d.groupby('mon')['t0_vr20'].rank(pct=True), [0, 1 / 3, 2 / 3, 1],
                  labels=['V1', 'V2', 'V3']).astype(str)


def _mk_key(cols):
    s = None
    for c in cols:
        v = d[c].astype('object').where(d[c].notna(), 'NA').astype(str)
        s = v if s is None else (s + '|' + v)
    return s.values


d['_mv'] = pd.cut(d.groupby('t0_date')['log_mv'].rank(pct=True), [0, 1 / 3, 2 / 3, 1],
                  labels=['S', 'M', 'L']).astype(object)
d['_r0'] = pd.cut(d.groupby('mon')['t0_ret'].rank(pct=True), [0, 1 / 3, 2 / 3, 1],
                  labels=['R1', 'R2', 'R3']).astype(object)
d['_v0'] = pd.cut(d.groupby('mon')['t0_vr20'].rank(pct=True), [0, 1 / 3, 2 / 3, 1],
                  labels=['V1', 'V2', 'V3']).astype(object)
KEY = {'A': _mk_key(['mon', '_mv', '_r0']),
       'B': _mk_key(['mon', 'regime', '_mv'])}


def matched(m, horizon='fwd5', sk='A'):
    m = np.asarray(m)
    df = pd.DataFrame({'_g': m, '_k': KEY[sk], '_v': d[horizon].values})
    df = df[df['_v'].notna()]
    g = df.groupby('_k')
    diffs, ws = [], []
    for k, gg in g:
        a = gg.loc[gg['_g'], '_v']
        b = gg.loc[~gg['_g'], '_v']
        if len(a) < 5 or len(b) < 20:
            continue
        diffs.append(float(a.median() - b.median()))
        ws.append(len(a))
    if not diffs:
        return np.nan, 0, np.nan, 0
    diffs = np.asarray(diffs); ws = np.asarray(ws, dtype=float)
    adv = float((diffs * ws).sum() / ws.sum())
    sd = diffs.std(ddof=1) if diffs.size > 1 else 0.0
    t = float(diffs.mean() / (sd / np.sqrt(diffs.size))) if sd > 0 else np.nan
    return adv, int(ws.sum()), t, len(diffs)


for name, m in CAND.items():
    for P in ['ALL', 'TRAIN', 'VALID', 'OOS']:
        pm = m if P == 'ALL' else (m & (d['period'].values == P))
        adv, nc, t, ns = matched(pm, 'fwd5', 'A')
        log = d.loc[pm, 'fwd5'].median()
        raw_n = d.loc[pm, 'fwd5'].notna().sum()
        base_log = d.loc[d['period'].values == P, 'fwd5'].median() if P != 'ALL' else d['fwd5'].median()
        CF.append({'section': 'CF_匹配', 'group': name, 'period': P,
                   'N_raw': int(raw_n), 'N_matched': nc,
                   'raw_med': log, 'base_med': base_log, 'raw_minus_base': log - base_log,
                   'matched_delta': adv, 'matched_t': t, 'n_strata': ns})
        adv2, nc2, t2, ns2 = matched(pm, 'fwd5', 'B')
        CF.append({'section': 'CF_匹配_regime', 'group': name, 'period': P,
                   'N_raw': int(raw_n), 'N_matched': nc2,
                   'raw_med': log, 'base_med': base_log, 'raw_minus_base': log - base_log,
                   'matched_delta': adv2, 'matched_t': t2, 'n_strata': ns2})

# ── §29 时间锚点审计 ──
for name, m in CAND.items():
    for P in ['ALL', 'TRAIN', 'VALID', 'OOS']:
        pm = m if P == 'ALL' else (m & (d['period'].values == P))
        for k in ANC:
            v = d.loc[pm, 'anc_' + k[:2]].dropna()
            if len(v) < 30:
                continue
            CF.append({'section': 'ANCHOR', 'group': name, 'period': P, 'anchor': k,
                       'N_raw': len(v), 'raw_med': float(v.median()),
                       'raw_minus_base': np.nan, 'matched_delta': np.nan,
                       'matched_t': np.nan, 'n_strata': 0})

# ── §30 Winner/Loser 事前可区分性（候选组内） ──
WLF = ['vr1', 'vr2', 'to2', 'cp2', 'body2', 'upper2', 'r1', 'r2', 'rs_cum12',
       'dma20_2', 'dma60_2', 'rng2', 'rvol20_2', 't0_ret', 't0_vr20', 't0_turnover',
       'vr_mean12', 'gap2', 'distC0_2', 'ma5slope_2']
WL = []
for name in ('C1_OVEREXT低20%', 'C2_OVEREXT低40%', 'C3_OVEREXT低40%+RS高40%', 'C8_cp2高20%'):
    m = CAND[name]
    sub = d[m & d['fwd5'].notna()]
    if len(sub) < 100:
        continue
    hi = sub[sub['fwd5'] >= sub['fwd5'].quantile(0.7)]
    lo = sub[sub['fwd5'] <= sub['fwd5'].quantile(0.3)]
    for f in WLF:
        a, b = hi[f].dropna(), lo[f].dropna()
        if len(a) < 20 or len(b) < 20:
            continue
        sp = (a.median() - b.median())
        sd = np.nanstd(np.concatenate([a.values, b.values]))
        WL.append({'section': 'WL', 'group': name, 'feature': f,
                   'winner_med': float(a.median()), 'loser_med': float(b.median()),
                   'diff': float(sp), 'std_gap': float(sp / sd) if sd > 0 else np.nan,
                   'N_win': len(a), 'N_los': len(b)})
WLD = pd.DataFrame(WL)
print('=== §30 Winner vs Loser 事前特征差（候选组内）===')
if not WLD.empty:
    print(WLD.sort_values('std_gap', key=lambda s: s.abs(), ascending=False).head(20).to_string(index=False))

# ── §24 Walk-Forward（数据起点 2021-04，窗口据实调整） ──
WF = []
WINS = [(2021, 2022, 2023), (2022, 2023, 2024), (2023, 2024, 2025), (2024, 2025, 2026)]
for name, m in list(CAND.items()) + [('A_全部首板(真实可交易)', np.ones(len(d), dtype=bool))]:
    for y0, y1, yt in WINS:
        tr = m & np.isin(d['yr'].values, [y0, y1])
        te = m & (d['yr'].values == yt)
        if te.sum() < 40:
            continue
        x_tr = st(d.loc[tr, 'fwd5'], cost_of(0.001))
        x_te = st(d.loc[te, 'fwd5'], cost_of(0.001))
        WF.append({'section': 'WF', 'group': name, 'window': '%d-%d->%d' % (y0, y1, yt),
                   'N_train': x_tr['N'], 'tr_med': x_tr.get('med'), 'tr_win': x_tr.get('win'),
                   'N_test': x_te['N'], 'te_med': x_te.get('med'), 'te_win': x_te.get('win'),
                   'te_pf': x_te.get('pf')})
WFD = pd.DataFrame(WF)
print('\n=== §24 Walk-Forward（T+5 中位数，含 0.255% 成本）===')
print(WFD.pivot_table(index='group', columns='window', values='te_med').mul(100).round(3).to_string())

CFD = pd.DataFrame(CF)
print('\n=== §28/§29 反事实 ===')
print(CFD[CFD.section == 'CF_匹配'].pivot_table(index='group', columns='period', values='matched_delta')
      .reindex(columns=['ALL', 'TRAIN', 'VALID', 'OOS']).mul(100).round(3).to_string())
print('\n-- raw_med - base_med（未匹配，对照）')
print(CFD[CFD.section == 'CF_匹配'].pivot_table(index='group', columns='period', values='raw_minus_base')
      .reindex(columns=['ALL', 'TRAIN', 'VALID', 'OOS']).mul(100).round(3).to_string())
print('\n=== §29 时间锚点审计（T+5 中位数 %，候选组）===')
an = CFD[CFD.section == 'ANCHOR']
print(an.pivot_table(index=['group', 'anchor'], columns='period', values='raw_med')
      .reindex(columns=['ALL', 'TRAIN', 'VALID', 'OOS']).mul(100).round(3).to_string())

CANDT = pd.DataFrame(rows)
print('\n=== 候选组 分期 T+5（含 0.255% 成本，中位数 %）===')
cc = CANDT[CANDT.section == 'CAND']
print(cc.pivot_table(index='group', columns='period', values='t5_med')
      .reindex(columns=['ALL', 'TRAIN', 'VALID', 'OOS']).mul(100).round(3).to_string())
print('\n-- 胜率')
print(cc.pivot_table(index='group', columns='period', values='t5_win')
      .reindex(columns=['ALL', 'TRAIN', 'VALID', 'OOS']).round(3).to_string())

cf_out = pd.concat([CFD, WLD], ignore_index=True)
cf_out.to_csv(os.path.join(ROOT, 'post_firstboard_alpha_counterfactual_%s.csv' % DATE), index=False)
CANDT.to_csv(os.path.join(ROOT, 'post_firstboard_alpha_oos_%s.csv' % DATE), index=False)
WFD.to_csv(os.path.join(ROOT, 'post_firstboard_alpha_walkforward_%s.csv' % DATE), index=False)
print('\n已写 counterfactual / oos / walkforward 三张 CSV')
