# -*- coding: utf-8 -*-
"""Post-FirstBoard Alpha V2.0 · 分层稳健性（§25 Regime / §26 市值 / §27 行业）+ §35.9 失败分析

原则
  * 分层仍是「条件切片」，不重估参数
  * 行业中性化用「月×行业」组内中位数去均值（同期截面，无未来信息）
  * 失败分析只做归因统计，不用于反向调参

输出
  research/post_firstboard_alpha_layer_YYYYMMDD.csv
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
ALL0 = d0.copy()
d = d0[d0['clean'] & (d0['listed_days'] >= 60) & d0['can_buy']].reset_index(drop=True)
comp = pd.read_parquet(os.path.join(OUT, 'pfb_comp.parquet'))
d = d.merge(comp.drop(columns=['period', 'mon']), on='event_id', how='left')
d['mon'] = (d['t0_date'] // 100).astype(int)
ri = d['_row'].values


def ck(m, k):
    return m[ri, k].astype(np.float64)


d['gap_entry'] = ck(M['op'], 3) / ck(M['cl'], 2) - 1.0

q = lambda col, p: d.groupby('mon')[col].rank(pct=True) <= p
QH = lambda col, p: d.groupby('mon')[col].rank(pct=True) >= 1 - p
CAND = {
    'A_全部首板': pd.Series(True, index=d.index),
    'C1_OVEREXT低20%': q('F_OVEREXT', 0.2),
    'C2_OVEREXT低40%': q('F_OVEREXT', 0.4),
    'C3_OVEREXT低40%+RS高40%': q('F_OVEREXT', 0.4) & QH('F_RS', 0.4),
    'C5_OVEREXTxVOL低20%': q('F_OVEREXT_x_VOL', 0.2),
    'C6_dma20_2低20%': q('dma20_2', 0.2),
    'Z1_OVEREXT高20%(反向对照)': QH('F_OVEREXT', 0.2),
    'Z2_VOLHEAT高20%(反向对照)': QH('F_VOLHEAT', 0.2),
}
CAND = {k: np.asarray(pd.Series(v).fillna(False)) for k, v in CAND.items()}

PER = ['ALL', 'TRAIN', 'VALID', 'OOS']
rows = []


def stat(mask, col):
    v = d.loc[mask, col]
    v = v[np.isfinite(v)]
    if len(v) == 0:
        return dict(N=0, t5_med=np.nan, t5_mean=np.nan, t5_win=np.nan)
    return dict(N=int(len(v)), t5_med=float(v.median()),
                t5_mean=float(v.mean()), t5_win=float((v > 0).mean()))


def emit(section, group, layer, mask, extra=None):
    for P in PER:
        s = mask if P == 'ALL' else (mask & (d['period'].values == P))
        r = {'section': section, 'group': group, 'layer': layer, 'period': P}
        r.update(stat(s, 'fwd5'))
        if extra:
            r.update(extra)
        rows.append(r)


# ══ §25 Regime 稳定性 ══
for g, mask in CAND.items():
    for rg in ['BULL', 'NORMAL', 'RANGE', 'BEAR']:
        emit('REGIME', g, rg, mask & (d['regime'].values == rg))

# ══ §26 市值稳定性 ══
for g, mask in CAND.items():
    for mg in ['Small', 'Mid', 'Large']:
        emit('MV', g, mg, mask & (d['mv_grp'].astype(str).values == mg))

# ══ §27 行业稳定性 ══
# 行业中性化：月×行业 组内中位数去均值（≥3 只），不足则退化为「月」中位数
key = d['mon'].astype(str) + '|' + d['industry'].astype(str)
gm = d.groupby(key)['fwd5'].transform('median')
gc = d.groupby(key)['fwd5'].transform('size')
bm = d.groupby('mon')['fwd5'].transform('median')
d['fwd5_iex'] = d['fwd5'] - np.where(gc >= 3, gm, bm)
d['fwd5_mex'] = d['fwd5'] - bm

for g in ['A_全部首板', 'C2_OVEREXT低40%', 'C3_OVEREXT低40%+RS高40%']:
    mask = CAND[g]
    sub = d[mask]
    # 行业明细（ALL，N>=30）
    for ind, s in sub.groupby('industry'):
        v = s['fwd5']
        v = v[np.isfinite(v)]
        if len(v) < 30:
            continue
        rows.append({'section': 'IND_DETAIL', 'group': g, 'layer': str(ind), 'period': 'ALL',
                     'N': int(len(v)), 't5_med': float(v.median()),
                     't5_mean': float(v.mean()), 't5_win': float((v > 0).mean())})
    # 集中度
    cnt = sub['industry'].value_counts()
    tot = cnt.sum()
    top1 = float(cnt.iloc[0] / tot) if len(cnt) else np.nan
    top3 = float(cnt.iloc[:3].sum() / tot) if len(cnt) else np.nan
    # 行业中性超额（同期截面去均值）
    for P in PER:
        s = sub if P == 'ALL' else sub[sub['period'] == P]
        b = d if P == 'ALL' else d[d['period'] == P]
        for tag, col in (('RAW', 'fwd5'), ('IEX_月行业中性', 'fwd5_iex'), ('MEX_月中性', 'fwd5_mex')):
            v = s[col]; v = v[np.isfinite(v)]
            can = CAND['A_全部首板']
            bb = b if P == 'ALL' else b
            m = bb[np.isfinite(bb[col])][col]
            rows.append({'section': 'IND_NEUT', 'group': g, 'layer': tag, 'period': P,
                         'N': int(len(v)),
                         't5_med': float(v.median()) if len(v) else np.nan,
                         't5_mean': float(v.mean()) if len(v) else np.nan,
                         't5_win': float((v > 0).mean()) if len(v) else np.nan,
                         'base_med': float(m.median()) if len(m) else np.nan,
                         'ex_delta': float(v.median() - m.median()) if len(v) and len(m) else np.nan})
    rows.append({'section': 'IND_CONC', 'group': g, 'layer': 'top1_share', 'period': 'ALL',
                 'N': int(tot), 't5_med': top1, 't5_mean': top3, 't5_win': np.nan})

# ══ §35.9 失败分析 + §20 MAE/MFE ══
# 覆盖度（全体事件，含不可交易）
cov = []
for lbl, m in (('全部首板事件', pd.Series(True, index=ALL0.index)),
               ('clean&n60', ALL0['clean'] & (ALL0['listed_days'] >= 60)),
               ('可交易(can_buy)', ALL0['clean'] & (ALL0['listed_days'] >= 60) & ALL0['can_buy'])):
    cov.append({'section': 'COVER', 'group': 'universe', 'layer': lbl, 'period': 'ALL',
                'N': int(m.sum()),
                't5_med': float(m.sum() / len(ALL0)), 't5_mean': np.nan, 't5_win': np.nan})
rows += cov

FAILS = {
    '冲高回落(mfe5>=5%且fwd5<0)': lambda s: (s['mfe5'] >= 0.05) & (s['fwd5'] < 0),
    '假突破(mfe5>=3%且fwd5<=-3%)': lambda s: (s['mfe5'] >= 0.03) & (s['fwd5'] <= -0.03),
    '放量滞涨(vr2月内前30%且fwd5<0)': lambda s: (s['vr2'] >= s['vr2'].quantile(0.7)) & (s['fwd5'] < 0),
    '高开低走(entry缺口>=2%且fwd5<0)': lambda s: (s['gap_entry'] >= 0.02) & (s['fwd5'] < 0),
    '极端波动(amp2月内前20%且fwd5<0)': lambda s: (s['amp2'] >= s['amp2'].quantile(0.8)) & (s['fwd5'] < 0),
    '流动性不足(entry额<2亿,单位千元)': lambda s: s['entry_amt'] < 2e5,
    '深度回撤(mae5<=-8%)': lambda s: s['mae5'] <= -0.08,
    '绝对亏损(fwd5<0)': lambda s: s['fwd5'] < 0,
    '大幅亏损(fwd5<=-5%)': lambda s: s['fwd5'] <= -0.05,
}
for g in ['A_全部首板', 'C2_OVEREXT低40%', 'C3_OVEREXT低40%+RS高40%']:
    sub = d[CAND[g]]
    for P in ['ALL', 'TRAIN', 'VALID', 'OOS']:
        s = sub if P == 'ALL' else sub[sub['period'] == P]
        n0 = len(s)
        for name, fn in FAILS.items():
            m = np.asarray(fn(s))
            rows.append({'section': 'FAILURE', 'group': g, 'layer': name, 'period': P,
                         'N': int(m.sum()), 't5_med': float(m.sum() / n0) if n0 else np.nan,
                         't5_mean': np.nan, 't5_win': np.nan})

# MAE/MFE 与完整分位（§20）
for g in ['A_全部首板', 'C2_OVEREXT低40%', 'C3_OVEREXT低40%+RS高40%']:
    sub = d[CAND[g]]
    for P in PER:
        s = sub if P == 'ALL' else sub[sub['period'] == P]
        for h in (3, 5, 10):
            v = s['fwd%d' % h]; v = v[np.isfinite(v)]
            a = s['mae%d' % h]; a = a[np.isfinite(a)]
            f = s['mfe%d' % h]; f = f[np.isfinite(f)]
            up = v[v > 0].sum(); dn = -v[v < 0].sum()
            rows.append({'section': 'DIST_T%d' % h, 'group': g, 'layer': 'fwd%d' % h, 'period': P,
                         'N': int(len(v)), 't5_med': float(np.median(v)) if len(v) else np.nan,
                         't5_mean': float(v.mean()) if len(v) else np.nan,
                         't5_win': float((v > 0).mean()) if len(v) else np.nan,
                         'p25': float(np.percentile(v, 25)) if len(v) else np.nan,
                         'p75': float(np.percentile(v, 75)) if len(v) else np.nan,
                         'mae': float(a.mean()) if len(a) else np.nan,
                         'mfe': float(f.mean()) if len(f) else np.nan,
                         'pf': float(up / dn) if dn > 1e-12 else np.inf})

L = pd.DataFrame(rows)
L.to_csv(os.path.join(ROOT, 'post_firstboard_alpha_layer_%s.csv' % DATE), index=False)

# ── 打印 ──
pd.set_option('display.width', 220)
print('=== §25 Regime 稳定性（T+5 中位数 %）===')
r = L[L.section == 'REGIME']
for col, tag in (('t5_med', 'med'), ('t5_win', 'win')):
    print('\n--', tag)
    t = r.pivot_table(index='group', columns=['period', 'layer'], values=col) if False else \
        r.pivot_table(index=['group', 'layer'], columns='period', values=col).reindex(columns=PER)
    print((t * (100 if col == 't5_med' else 1)).round(3).to_string())
print('\n-- Regime N')
print(r.pivot_table(index=['group', 'layer'], columns='period', values='N').reindex(columns=PER).to_string())

print('\n=== §26 市值稳定性（T+5 中位数 %）===')
m = L[L.section == 'MV']
print(m.pivot_table(index=['group', 'layer'], columns='period', values='t5_med')
      .reindex(columns=PER).mul(100).round(3).to_string())
print('\n-- 市值 N')
print(m.pivot_table(index=['group', 'layer'], columns='period', values='N').reindex(columns=PER).to_string())

print('\n=== §27 行业中性（T+5 中位数 % / 超额 pp）===')
nn = L[L.section == 'IND_NEUT']
for P in ['TRAIN', 'VALID', 'OOS', 'ALL']:
    t = nn[nn.period == P][['group', 'layer', 'N', 't5_med', 'ex_delta', 't5_win']]
    t = t.copy()
    t['t5_med'] = (t['t5_med'] * 100).round(3)
    t['ex_delta'] = (t['ex_delta'] * 100).round(3)
    t['t5_win'] = t['t5_win'].round(3)
    print('\n--', P)
    print(t.to_string(index=False))
print('\n-- 行业集中度（top1/top3 占比）')
print(L[L.section == 'IND_CONC'][['group', 'N', 't5_med', 't5_mean']].round(4).to_string(index=False))

print('\n=== §27 C3 行业明细（N>=30, T+5 中位数 %）===')
dd = L[(L.section == 'IND_DETAIL') & (L.group == 'C3_OVEREXT低40%+RS高40%')].copy()
dd['t5_med'] = (dd.t5_med * 100).round(3)
print(dd.sort_values('N', ascending=False)[['layer', 'N', 't5_med', 't5_win']].to_string(index=False))

print('\n=== §35.9 失败分析（占比）===')
f = L[L.section == 'FAILURE']
piv = f.pivot_table(index=['group', 'layer'], columns='period', values='t5_med').reindex(columns=PER)
print(piv.round(4).to_string())
print('\n-- 绝对计数 ALL')
print(f[f.period == 'ALL'][['group', 'layer', 'N']].to_string(index=False))

print('\n=== §20 分布 MAE/MFE（%）===')
for h in (3, 5, 10):
    t = L[L.section == 'DIST_T%d' % h]
    print('\n-- T+%d' % h)
    z = t.pivot_table(index='group', columns='period',
                      values=['t5_med', 't5_mean', 'mae', 'mfe', 'pf'])
    print(z.reindex(columns=PER, level=1).round(4).to_string())
