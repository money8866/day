# -*- coding: utf-8 -*-
"""突破前横截面 Alpha · 核心统计（§14 §16 §17 §18 §19 §21）

输入 out/brk_cand.parquet
输出
  out/brk_daily.parquet   每个 (feature, horizon) 的逐日统计（IC / TopK / Winner-Loser）
  out/brk_stat.parquet    按 TRAIN / VALID / OOS / Regime / ALL 汇总

口径说明
  §17 每日横截面 Rank IC: 严格在「当日有效样本」内取平均秩, 再对秩向量求 Pearson
      (= Spearman)。不使用全样本秩近似。
  §18 Top/Bottom-K 按当日秩 pct 分档 (0.9/0.8/0.2/0.1)。
  §19 Winner = 当日 T+H 收益秩 pct>=0.8, Loser = pct<=0.2; AUC = Mann-Whitney U
      在 W∪L 的并集内取秩计算, 标签仅用于验证。
  §21 基准 B1 全体候选 / B2 随机序 / B3 纯动量 / B4 纯距离 / B5 纯量能 / B6 简单突破。
"""
import os
import json
import numpy as np
import pandas as pd

np.seterr(all='ignore')
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
HOR = (1, 3, 5, 10)

meta = json.load(open(os.path.join(OUT, 'brk_meta.json'), encoding='utf-8'))
FEATS = [f['name'] for f in meta['features']]
FAM = {f['name']: f['family'] for f in meta['features']}

print('读取 brk_cand ...', flush=True)
df = pd.read_parquet(os.path.join(OUT, 'brk_cand.parquet'))
df = df.sort_values(['trade_date', 'ts_code'], kind='mergesort').reset_index(drop=True)
m = len(df)
dt = df['trade_date'].values.astype(np.int64)
di = df['di'].values.astype(np.int64)
gd, dayidx = np.unique(di, return_inverse=True)
dayidx = dayidx.astype(np.int64)
nD = len(gd)
DPD = np.bincount(dayidx, minlength=nD)              # 每日候选行数
day_date = np.zeros(nD, dtype=np.int64)
day_date[dayidx] = dt
day_date = dt[np.searchsorted(dayidx, np.arange(nD))]
print('rows=%d days=%d feats=%d' % (m, nD, len(FEATS)), flush=True)

Y = {H: df['fwd_%d' % H].values.astype(np.float64) for H in HOR}
BV = np.ones(m, dtype=bool)
for H in HOR:
    BV &= np.isfinite(Y[H])
print('common valid rows = %d (%.1f%%)' % (BV.sum(), 100.0 * BV.mean()), flush=True)

# ── §21 基准列
rng = np.random.default_rng(20260925)
df['B2_RandomOrder'] = rng.random(m)
df['B3_Momentum_Ret20'] = df['Ret_20'].values
df['B4_DistToResistance'] = -df['Distance_to_R20'].values
df['B5_Volume_VR20'] = df['Vol_over_MA20'].values
df['B6_SimpleBreakoutFlag'] = (df['Distance_to_R20'].values > 0).astype(float)
BASE = ['B2_RandomOrder', 'B3_Momentum_Ret20', 'B4_DistToResistance', 'B5_Volume_VR20',
        'B6_SimpleBreakoutFlag']
for nm in BASE:
    if nm not in FEATS:
        FEATS.append(nm)
        FAM[nm] = 'baseline'


def aggd(v):
    """按日求和, 空日 → 0 (bincount 天然处理空组, 避免 reduceat 的空组错位)"""
    return np.bincount(dayidx, weights=np.asarray(v, dtype=np.float64), minlength=nD)


def sdv(a, b):
    return np.where(np.abs(b) > 1e-12, a / b, np.nan)


def rank_day(x, mask):
    """当日平均秩 (1..n); 非 mask 行为 nan。O(n log n), 全程 numpy。"""
    res = np.full(m, np.nan)
    idx = np.flatnonzero(mask)
    if idx.size < 2:
        return res
    d = dayidx[idx]
    v = x[idx]
    order = np.lexsort((v, d))
    ds = d[order]
    vs = v[order]
    news = np.r_[True, ds[1:] != ds[:-1]]
    gb = np.flatnonzero(news)
    gstart = np.repeat(gb, np.diff(np.r_[gb, idx.size]))
    posg = np.arange(idx.size, dtype=np.float64) - gstart
    newrun = np.r_[True, (ds[1:] != ds[:-1]) | (vs[1:] != vs[:-1])]
    rid = np.cumsum(newrun) - 1
    nr = rid[-1] + 1
    s = np.bincount(rid, weights=posg, minlength=nr)
    c = np.bincount(rid, minlength=nr).astype(np.float64)
    res[idx[order]] = s[rid] / c[rid] + 1.0
    return res


# ── 自检: rank_day 必须与 pandas 逐日 rank 一致
_chk = pd.Series(df['Ret_5'].values)
_ck = _chk.groupby(dayidx).rank().values
_rk = rank_day(df['Ret_5'].values.astype(np.float64), np.isfinite(_chk.values))
_ok = np.allclose(_ck, _rk, equal_nan=True)
print('rank_day 自检: %s' % ('PASS' if _ok else 'FAIL'), flush=True)
assert _ok, 'rank_day 与 pandas 不一致'

periods = {'ALL': np.ones(nD, dtype=bool),
           'TRAIN': (day_date >= 20210401) & (day_date <= 20231231),
           'VALID': (day_date >= 20240101) & (day_date <= 20251231),
           'OOS': (day_date >= 20260101)}
reg_day = df['Regime'].values[np.searchsorted(dayidx, np.arange(nD))]
for r in ('BULL', 'NORMAL', 'BEAR'):
    periods[r] = (reg_day == r)

DAILY = []
STAT = []


def ap(x, sel):
    v = np.asarray(x, dtype=np.float64)[sel]
    v = v[np.isfinite(v)]
    d = {'n_days': int(v.size), 'mean': np.nan, 'median': np.nan,
         'std': np.nan, 'icir': np.nan, 'pos_ratio': np.nan}
    if v.size == 0:
        return d
    d['mean'] = float(v.mean())
    d['median'] = float(np.median(v))
    if v.size > 1:
        s = float(v.std(ddof=1))
        d['std'] = s
        d['icir'] = float(v.mean() / s) if s > 0 else np.nan
    d['pos_ratio'] = float((v > 0).mean())
    return d


def run_feature(nm):
    f = df[nm].values.astype(np.float64)
    Vf = BV & np.isfinite(f)
    for H in HOR:
        yy = Y[H]
        VH = Vf & np.isfinite(yy)
        if VH.sum() < 50:
            continue
        rf = rank_day(f, VH)                     # 当日秩 (有效子集内)
        ry = rank_day(yy, VH)
        ndrow = np.repeat(aggd(VH.astype(float)), DPD)
        pct = sdv(rf, ndrow)
        n = aggd(VH.astype(float))
        a = np.where(VH, rf, 0.0)
        b = np.where(VH, ry, 0.0)
        sa = aggd(a); sb = aggd(b); sab = aggd(a * b)
        saa = aggd(a * a); sbb = aggd(b * b)
        num = n * sab - sa * sb
        den = np.sqrt(np.maximum(n * saa - sa ** 2, 0) * np.maximum(n * sbb - sb ** 2, 0))
        ic = sdv(num, den)
        # ── §18 Top/Bottom-K
        tk = {}
        for kn, msk in (('t10', VH & (pct >= 0.9)), ('b10', VH & (pct <= 0.1)),
                        ('t20', VH & (pct >= 0.8)), ('b20', VH & (pct <= 0.2))):
            cnt = aggd(msk.astype(float))
            sy = aggd(np.where(msk, yy, 0.0))
            sw = aggd(np.where(msk & (yy > 0), 1.0, 0.0))
            tk[kn] = (sdv(sy, cnt), sdv(sw, cnt))
        # ── §19 Winner / Loser
        wm = VH & (ry >= ndrow * 0.8)
        lm = VH & (ry <= ndrow * 0.2)
        nW = aggd(wm.astype(float))
        nL = aggd(lm.astype(float))
        fW = aggd(np.where(wm, f, 0.0)); fL = aggd(np.where(lm, f, 0.0))
        mW = sdv(fW, nW); mL = sdv(fL, nL)
        ok2 = (nW > 0) & (nL > 0)
        # AUC 需在 W∪L 并集内取秩 (Mann-Whitney U)
        rwl = rank_day(f, wm | lm)
        rW = aggd(np.where(wm, rwl, 0.0)); rL = aggd(np.where(lm, rwl, 0.0))
        auc = np.where(ok2, sdv(rW - nW * (nW + 1) / 2.0, nW * nL), np.nan)
        # 秩分离度: W 平均 pct - L 平均 pct
        rfW = aggd(np.where(wm, rf, 0.0)); rfL = aggd(np.where(lm, rf, 0.0))
        rank_sep = np.where(ok2, sdv(sdv(rfW, nW) - sdv(rfL, nL), n), np.nan)
        # 标准化组间差
        sx = aggd(np.where(VH, f, 0.0)); sxx = aggd(np.where(VH, f * f, 0.0))
        md = sdv(sx, n); sd = np.sqrt(np.maximum(sdv(sxx, n) - md ** 2, 0.0))
        win_z = sdv(mW - md, sd); los_z = sdv(mL - md, sd)
        std_gap = win_z - los_z
        eff = 2.0 * auc - 1.0
        DAILY.append(pd.DataFrame({
            'feature': nm, 'horizon': H, 'day': np.arange(nD), 'date': day_date,
            'ic': ic, 'n': n,
            'top10': tk['t10'][0], 'bot10': tk['b10'][0],
            'top20': tk['t20'][0], 'bot20': tk['b20'][0],
            'wr_top10': tk['t10'][1], 'wr_top20': tk['t20'][1],
            'win_z': win_z, 'los_z': los_z, 'std_gap': std_gap,
            'auc': auc, 'rank_sep': rank_sep, 'effect_size': eff,
            'win_mean': mW, 'los_mean': mL, 'nW': nW, 'nL': nL}))
        for pn, sel in periods.items():
            gi = ap(ic, sel)
            gs = ap(tk['t10'][0] - tk['b10'][0], sel)
            gs2 = ap(tk['t20'][0] - tk['b20'][0], sel)
            STAT.append({
                'feature': nm, 'family': FAM.get(nm, 'other'), 'horizon': H, 'period': pn,
                'n_days': gi['n_days'],
                'n_rows': int(np.asarray(aggd(VH.astype(float)))[sel].sum()),
                'ic_mean': gi['mean'], 'ic_median': gi['median'], 'ic_std': gi['std'],
                'icir': gi['icir'], 'ic_pos': gi['pos_ratio'],
                'top10_ret': ap(tk['t10'][0], sel)['mean'],
                'bot10_ret': ap(tk['b10'][0], sel)['mean'],
                'top20_ret': ap(tk['t20'][0], sel)['mean'],
                'bot20_ret': ap(tk['b20'][0], sel)['mean'],
                'top10_med': ap(tk['t10'][0], sel)['median'],
                'bot10_med': ap(tk['b10'][0], sel)['median'],
                'spread10': gs['mean'], 'spread10_med': gs['median'],
                'spread10_pos': gs['pos_ratio'], 'spread10_std': gs['std'],
                'spread20': gs2['mean'], 'spread20_med': gs2['median'],
                'wr_top10': ap(tk['t10'][1], sel)['mean'],
                'auc': ap(auc, sel)['mean'], 'rank_sep': ap(rank_sep, sel)['mean'],
                'std_gap': ap(std_gap, sel)['mean'], 'effect_size': ap(eff, sel)['mean'],
                'win_mean': ap(mW, sel)['mean'], 'los_mean': ap(mL, sel)['mean']})


# ── §21 Baseline 1: 全体候选
print('B1_AllCandidates', flush=True)
for H in HOR:
    yy = Y[H]
    fin = np.isfinite(yy)
    dmean = sdv(aggd(np.where(fin, yy, 0.0)), aggd(fin.astype(float)))
    z = np.full(nD, np.nan)
    DAILY.append(pd.DataFrame({
        'feature': 'B1_AllCandidates', 'horizon': H, 'day': np.arange(nD), 'date': day_date,
        'ic': np.nan, 'n': aggd(fin.astype(float)),
        'top10': np.nan, 'bot10': np.nan, 'top20': np.nan, 'bot20': np.nan,
        'wr_top10': np.nan, 'wr_top20': np.nan, 'win_z': np.nan, 'los_z': np.nan,
        'std_gap': np.nan, 'auc': np.nan, 'rank_sep': np.nan, 'effect_size': np.nan,
        'win_mean': z, 'los_mean': z, 'nW': np.nan, 'nL': np.nan}))
    for pn, sel in periods.items():
        d = ap(dmean, sel)
        STAT.append({'feature': 'B1_AllCandidates', 'family': 'baseline', 'horizon': H,
                     'period': pn, 'n_days': d['n_days'],
                     'n_rows': int(np.asarray(aggd(fin.astype(float)))[sel].sum()),
                     'ic_mean': np.nan, 'ic_median': np.nan, 'ic_std': np.nan, 'icir': np.nan,
                     'ic_pos': np.nan, 'top10_ret': np.nan, 'bot10_ret': np.nan,
                     'top20_ret': np.nan, 'bot20_ret': np.nan, 'top10_med': np.nan,
                     'bot10_med': np.nan, 'spread10': np.nan, 'spread10_med': d['median'],
                     'spread10_pos': np.nan, 'spread10_std': np.nan, 'spread20': np.nan,
                     'spread20_med': np.nan, 'wr_top10': np.nan, 'auc': np.nan,
                     'rank_sep': np.nan, 'std_gap': np.nan, 'effect_size': np.nan,
                     'win_mean': np.nan, 'los_mean': np.nan, 'mean_ret': d['mean']})

for fi, nm in enumerate(FEATS):
    print('  [%d/%d] %s' % (fi + 1, len(FEATS), nm), flush=True)
    run_feature(nm)

print('汇总 ...', flush=True)
D = pd.concat(DAILY, ignore_index=True)
D.to_parquet(os.path.join(OUT, 'brk_daily.parquet'), index=False, compression='zstd')
S = pd.DataFrame(STAT)
S.to_parquet(os.path.join(OUT, 'brk_stat.parquet'), index=False, compression='zstd')
print('已写 brk_daily.parquet', D.shape, ' brk_stat.parquet', S.shape, flush=True)

pd.set_option('display.width', 260)
print('\n=== B1 全体候选 日均收益（未扣成本）===')
print(S[S.feature == 'B1_AllCandidates'][['horizon', 'period', 'mean_ret', 'spread10_med', 'n_days']]
      .to_string(index=False))

CAND = S[S.feature != 'B1_AllCandidates'].copy()
for H in (3, 5):
    t = CAND[(CAND.horizon == H) & (CAND.period == 'ALL')].copy()
    t['absic'] = t['ic_mean'].abs()
    print('\n=== H=%d |IC| 排名（ALL）===' % H)
    print(t.sort_values('absic', ascending=False)
          [['feature', 'family', 'ic_mean', 'icir', 'ic_pos', 'spread10', 'spread20',
            'auc', 'rank_sep', 'std_gap']].head(25).to_string(index=False))
    print('\n=== H=%d spread10 排名（ALL）===' % H)
    print(t.sort_values('spread10', ascending=False)
          [['feature', 'family', 'ic_mean', 'top10_ret', 'bot10_ret', 'spread10',
            'spread20', 'wr_top10', 'auc']].head(20).to_string(index=False))
    print('\n=== H=%d AUC 偏离 0.5 排名（ALL）===' % H)
    t['auc_dev'] = (t['auc'] - 0.5).abs()
    print(t.sort_values('auc_dev', ascending=False)
          [['feature', 'family', 'ic_mean', 'auc', 'rank_sep', 'std_gap', 'spread10']]
          .head(20).to_string(index=False))
    for pn in ('TRAIN', 'VALID', 'OOS'):
        t2 = CAND[(CAND.horizon == H) & (CAND.period == pn)]
        print('\n=== H=%d spread10 排名（%s）===' % (H, pn))
        print(t2.sort_values('spread10', ascending=False)
              [['feature', 'family', 'ic_mean', 'spread10', 'spread20', 'auc', 'rank_sep']]
              .head(15).to_string(index=False))
