# -*- coding: utf-8 -*-
"""Post-FirstBoard Alpha V2.0 · 第一阶段单因子扫描（§12/§13/§14）

方法（关键）
  * 全部收益均从 ENTRY = T+3 开盘 起算，持有 3/5/10 交易日
  * 所有统计先在「月内截面」做分层/排名，再跨月聚合 → 彻底剔除时点效应
    （避免重蹈「相邻日期样本抱团 → 伪 Alpha」的覆辙）
  * IC = 月内 Spearman(feature, fwd)；报告 IC 均值 / ICIR / 正比例
  * 分位 = 月内 rank 五分位；报告各分位 fwd 中位数与单调性
  * 不预设方向，不选历史最优阈值

输出
  research/post_firstboard_alpha_features_YYYYMMDD.csv
  research/post_firstboard_alpha_quantiles_YYYYMMDD.csv
"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
DATE = '20260925'
ROOT = os.path.dirname(HERE)

d = pd.read_parquet(os.path.join(OUT, 'pfb_feat.parquet'))
d = d[d['clean'] & (d['listed_days'] >= 60) & d['can_buy']].copy()
d['mon'] = (d['t0_date'] // 100).astype(int)
print('样本', len(d), '| 月数', d['mon'].nunique())
print(d['period'].value_counts().to_string())

DROP = {'event_id', 'ts_code', 'name', 'industry', 'board', 'trade_date', 't0_date',
        'yr', 'period', 'clean', 'listed_days', 'mv_grp', 'to_grp', 'regime', 'mon',
        'entry_open', 'entry_limit', 'entry_oneword', 'entry_amt', 'entry_gap',
        'entry_to', 'can_buy', 'liq_ok', 'log_mv', 't0_ret20prev', 'one_word',
        'opened_board', 'limit_pct', 'amplitude'}
TGT_PRE = ('fwd', 'mae', 'mfe', 'd1_ret', 't0_fwd', 'sig_fwd', 't0_ret20prev',
           'mon', '_rk')
FL = [c for c in d.columns
      if c not in DROP and d[c].dtype.kind in 'fi'
      and not any(c.startswith(p) for p in TGT_PRE)]
# 需要全部目标列参与 IC 排名计算，故显式声明目标
FL = [c for c in FL if c in d.columns]
print('候选特征数', len(FL))
cov = {c: float(d[c].notna().mean()) for c in FL}
print('覆盖率 <0.5 的特征:', {k: round(v, 3) for k, v in cov.items() if v < 0.5})

HORS = [3, 5, 10]
tgt = {N: 'fwd%d' % N for N in HORS}
PER = ['TRAIN', 'VALID', 'OOS']

# ── 月内排名（每个目标只算一次） ──
rk = {}
for N in HORS:
    d['_rk%d' % N] = d.groupby('mon')[tgt[N]].rank(pct=True)
for f in FL:
    d['_f'] = d.groupby('mon')[f].rank(pct=True)
    rk[f] = d['_f'].copy()
d.drop(columns=['_f'], inplace=True)


def monthly_ic(f, N, sub=None):
    """月内 Spearman(feature, fwd) 序列"""
    dd = d if sub is None else d[sub]
    a = rk[f][dd.index]
    b = dd['_rk%d' % N]
    g = dd.groupby('mon')
    ok = a.notna() & b.notna()
    n = ok.groupby(dd['mon']).sum()
    num = (a[ok] * b[ok]).groupby(dd.loc[ok, 'mon']).mean()
    ma = a[ok].groupby(dd.loc[ok, 'mon']).mean()
    mb = b[ok].groupby(dd.loc[ok, 'mon']).mean()
    cov = num - ma * mb
    sa = a[ok].groupby(dd.loc[ok, 'mon']).std(ddof=0)
    sb = b[ok].groupby(dd.loc[ok, 'mon']).std(ddof=0)
    ic = (cov / (sa * sb)).where(n >= 20)
    return ic.dropna()


rows = []
for f in FL:
    rec = {'feature': f}
    for N in HORS:
        ic_all = monthly_ic(f, N)
        rec['ic%d_mean' % N] = ic_all.mean()
        rec['ic%d_icir' % N] = ic_all.mean() / ic_all.std(ddof=1) if ic_all.std(ddof=1) > 0 else np.nan
        rec['ic%d_pos' % N] = (ic_all > 0).mean()
        rec['ic%d_nmon' % N] = len(ic_all)
    for P in PER:
        sub = d['period'] == P
        ic = monthly_ic(f, 5, sub)
        rec['ic5_%s' % P] = ic.mean()
        ic3 = monthly_ic(f, 3, sub)
        rec['ic3_%s' % P] = ic3.mean()
        rec['n_%s' % P] = int(sub.sum())
    rows.append(rec)

ft = pd.DataFrame(rows)
# 稳定性：TRAIN/VALID 同号，且 |OOS| 不显著反向
s_tr, s_va, s_oo = np.sign(ft['ic5_TRAIN']), np.sign(ft['ic5_VALID']), np.sign(ft['ic5_OOS'])
ft['stab_core'] = (s_tr == s_va) & (s_tr != 0)
ft['stab_oos_ok'] = ft['stab_core'] & ((s_oo == s_tr) | (ft['ic5_OOS'].abs() < 0.01))
ft['rank_abs'] = ft['ic5_mean'].abs()
ft = ft.sort_values('rank_abs', ascending=False)
ft.to_csv(os.path.join(ROOT, 'post_firstboard_alpha_features_%s.csv' % DATE), index=False)
print('\n=== 单因子 IC 排名（|ic5_all| 前 25） ===')
cols = ['feature', 'ic5_mean', 'ic5_icir', 'ic5_pos', 'ic3_mean', 'ic10_mean',
        'ic5_TRAIN', 'ic5_VALID', 'ic5_OOS', 'stab_core', 'stab_oos_ok']
print(ft[cols].head(25).to_string(index=False))
print('\n=== TRAIN/VALID 同号 且 OOS 未反向 的特征 ===')
good = ft[ft['stab_oos_ok']].sort_values('rank_abs', ascending=False)
print(good[cols].head(30).to_string(index=False))
print('计数:', len(good), '/', len(ft))

# ── 五分位收益（月内分位） ──
qr = []
for f in FL:
    if cov[f] < 0.5:
        continue
    dd = d[rk[f].notna()].copy()
    if dd.empty:
        continue
    dd['q'] = pd.qcut(rk[f][dd.index].rank(method='first'), 5, labels=False) + 1
    for P in ['ALL'] + PER:
        s = dd if P == 'ALL' else dd[dd['period'] == P]
        for N in HORS:
            g = s.groupby('q')[tgt[N]]
            med = g.median()
            for q in range(1, 6):
                qr.append({'feature': f, 'period': P, 'horizon': 'T+%d' % N,
                           'quantile': 'Q%d' % q, 'N': int(g.size().get(q, 0)),
                           'med': float(med.get(q, np.nan)),
                           'mean': float(g.mean().get(q, np.nan)),
                           'win': float((g.apply(lambda x: (x > 0).mean())).get(q, np.nan))})
qt = pd.DataFrame(qr)
qt.to_csv(os.path.join(ROOT, 'post_firstboard_alpha_quantiles_%s.csv' % DATE), index=False)

# ── 单调性 ──
mon_rows = []
for f in FL:
    if cov[f] < 0.5:
        continue
    for P in ['ALL'] + PER:
        s = qt[(qt['feature'] == f) & (qt['period'] == P) & (qt['horizon'] == 'T+5')]
        if len(s) < 5:
            continue
        v = s.sort_values('quantile')['med'].values
        rho = pd.Series(v).rank().corr(pd.Series(range(5)).rank())
        # 非线性形状
        dq = np.diff(v)
        mono = bool(np.all(dq > 0) or np.all(dq < 0))
        shape = 'MONO_UP' if (mono and dq[0] > 0) else ('MONO_DN' if mono else (
            'U' if (dq[0] < 0 and dq[-1] > 0) else ('INV_U' if (dq[0] > 0 and dq[-1] < 0) else 'NONMONO')))
        mon_rows.append({'feature': f, 'period': P, 'Q_med': list(np.round(v * 100, 3)),
                         'rho': rho, 'mono': mono, 'shape': shape})
mt = pd.DataFrame(mon_rows)
print('\n=== 非线性形状（ALL, T+5） ===')
print(mt[mt['period'] == 'ALL']['shape'].value_counts().to_string())
print(mt[(mt['period'] == 'ALL') & (mt['shape'].isin(['MONO_UP', 'MONO_DN']))]
      .sort_values('rho', ascending=False).head(20).to_string(index=False))
mt.to_csv(os.path.join(OUT, 'pfb_shapes.csv'), index=False)
print('\n已写 post_firstboard_alpha_features / quantiles')
