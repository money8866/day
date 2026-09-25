# -*- coding: utf-8 -*-
"""突破前横截面 Alpha · 深度检验（§22 §24 §25 §26 §27 §28 §29 §35）

输入 out/brk_cand.parquet + out/brk_stat.parquet + out/brk_daily.parquet
输出（§35 命名）
  breakout_alpha_daily_ic.csv
  breakout_alpha_walkforward.csv
  breakout_alpha_parameter_grid.csv
  breakout_alpha_counterfactual.csv
  breakout_alpha_regime.csv
  breakout_alpha_cost.csv
  breakout_alpha_stability.csv
  breakout_alpha_interaction.csv
  out/brk_finalists.json
"""
import os
import json
import numpy as np
import pandas as pd

np.seterr(all='ignore')
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
FEE = 0.00055
SLIPS = (0.0, 0.001, 0.002, 0.003)
HOR = (3, 5)


def cost_of(slip):
    return 2.0 * slip + FEE


print('读取 ...', flush=True)
df = pd.read_parquet(os.path.join(OUT, 'brk_cand.parquet'))
df = df.sort_values(['trade_date', 'ts_code'], kind='mergesort').reset_index(drop=True)
m = len(df)
dt = df['trade_date'].values.astype(np.int64)
di = df['di'].values.astype(np.int64)
gd, dayidx = np.unique(di, return_inverse=True)
dayidx = dayidx.astype(np.int64)
nD = len(gd)
DPD = np.bincount(dayidx, minlength=nD)
day_date = dt[np.searchsorted(dayidx, np.arange(nD))]
Y = {H: df['fwd_%d' % H].values.astype(np.float64) for H in (1, 3, 5, 10)}
BV = np.ones(m, dtype=bool)
for H in (1, 3, 5, 10):
    BV &= np.isfinite(Y[H])
reg_day = df['Regime'].values[np.searchsorted(dayidx, np.arange(nD))]
S = pd.read_parquet(os.path.join(OUT, 'brk_stat.parquet'))
D = pd.read_parquet(os.path.join(OUT, 'brk_daily.parquet'))
print('cand=%s  days=%d' % (df.shape, nD), flush=True)

def aggd(v):
    return np.bincount(dayidx, weights=np.asarray(v, dtype=np.float64), minlength=nD)


def sdv(a, b):
    return np.where(np.abs(b) > 1e-12, a / b, np.nan)


def rank_day(x, mask):
    res = np.full(m, np.nan)
    idx = np.flatnonzero(mask)
    if idx.size < 2:
        return res
    d = dayidx[idx]
    v = np.asarray(x, dtype=np.float64)[idx]
    order = np.lexsort((v, d))
    ds = d[order]; vs = v[order]
    gb = np.flatnonzero(np.r_[True, ds[1:] != ds[:-1]])
    gstart = np.repeat(gb, np.diff(np.r_[gb, idx.size]))
    posg = np.arange(idx.size, dtype=np.float64) - gstart
    newrun = np.r_[True, (ds[1:] != ds[:-1]) | (vs[1:] != vs[:-1])]
    rid = np.cumsum(newrun) - 1
    nr = rid[-1] + 1
    s = np.bincount(rid, weights=posg, minlength=nr)
    c = np.bincount(rid, minlength=nr).astype(np.float64)
    res[idx[order]] = s[rid] / c[rid] + 1.0
    return res


def daily_eval(sig, H):
    """返回 (ic, spread10, spread20, auc, top10, bot10, n) 逐日数组"""
    yy = Y[H]
    V = BV & np.isfinite(sig) & np.isfinite(yy)
    rs = rank_day(sig, V)
    ry = rank_day(yy, V)
    ndrow = np.repeat(aggd(V.astype(float)), DPD)
    pct = sdv(rs, ndrow)
    n = aggd(V.astype(float))
    a = np.where(V, rs, 0.0); b = np.where(V, ry, 0.0)
    sa = aggd(a); sb = aggd(b); sab = aggd(a * b)
    saa = aggd(a * a); sbb = aggd(b * b)
    den = np.sqrt(np.maximum(n * saa - sa ** 2, 0) * np.maximum(n * sbb - sb ** 2, 0))
    ic = sdv(n * sab - sa * sb, den)
    t10 = V & (pct >= 0.9); b10 = V & (pct <= 0.1)
    t20 = V & (pct >= 0.8); b20 = V & (pct <= 0.2)
    mt10 = sdv(aggd(np.where(t10, yy, 0.0)), aggd(t10.astype(float)))
    mb10 = sdv(aggd(np.where(b10, yy, 0.0)), aggd(b10.astype(float)))
    mt20 = sdv(aggd(np.where(t20, yy, 0.0)), aggd(t20.astype(float)))
    mb20 = sdv(aggd(np.where(b20, yy, 0.0)), aggd(b20.astype(float)))
    wmm = V & (ry >= ndrow * 0.8); lmm = V & (ry <= ndrow * 0.2)
    nW = aggd(wmm.astype(float)); nL = aggd(lmm.astype(float))
    rwl = rank_day(sig, wmm | lmm)
    auc = np.where((nW > 0) & (nL > 0),
                   sdv(aggd(np.where(wmm, rwl, 0.0)) - nW * (nW + 1) / 2.0, nW * nL), np.nan)
    return ic, mt10 - mb10, mt20 - mb20, auc, mt10, mb10, n


def summ(v, sel=None):
    v = np.asarray(v, dtype=np.float64)
    if sel is not None:
        v = v[sel]
    v = v[np.isfinite(v)]
    if v.size == 0:
        return dict(n=0, mean=np.nan, med=np.nan, wr=np.nan, pos=np.nan, t=np.nan)
    t = float(v.mean() / (v.std(ddof=1) / np.sqrt(v.size))) if v.size > 1 and v.std(ddof=1) > 0 else np.nan
    return dict(n=int(v.size), mean=float(v.mean()), med=float(np.median(v)),
                wr=float((v > 0).mean()), pos=float((v > 0).mean()), t=t)


# ══════════════ §35 daily IC 明细
print('导出 daily_ic ...', flush=True)
D[D.feature != 'B1_AllCandidates'][['feature', 'horizon', 'date', 'ic', 'n', 'auc',
                                    'win_z', 'los_z', 'top10', 'bot10']] \
    .to_csv(os.path.join(HERE, 'breakout_alpha_daily_ic.csv'), index=False, encoding='utf-8-sig')

# ══════════════ §35 OOS 明细（TRAIN / VALID / OOS 三期并列）
print('导出 OOS ...', flush=True)
_S = S[S.feature != 'B1_AllCandidates']
_items = []
for col in ('n_days', 'ic_mean', 'icir', 'ic_pos', 'top10_ret', 'bot10_ret', 'spread10',
            'spread10_pos', 'auc', 'win_mean', 'los_mean', 'std_gap', 'effect_size'):
    q = _S.pivot_table(index=['feature', 'family', 'horizon'], columns='period', values=col)
    q = q[[c for c in ('TRAIN', 'VALID', 'OOS') if c in q.columns]]
    q.columns = ['%s_%s' % (col, c) for c in q.columns]
    _items.append(q)
OOSDF = pd.concat(_items, axis=1).reset_index()
OOSDF.to_csv(os.path.join(HERE, 'breakout_alpha_oos.csv'), index=False, encoding='utf-8-sig')
print('  OOS %s' % (OOSDF.shape,), flush=True)

# ══════════════ 候选筛选（透明规则：三期同号 + |IC| 下限 + AUC 下限 + 每族最多 2 个）
cand = S[S.feature != 'B1_AllCandidates']
fin = []
for H in HOR:
    t = cand[cand.horizon == H]
    p = t.pivot_table(index='feature', columns='period',
                      values=['ic_mean', 'spread10', 'auc'], aggfunc='mean')
    rows = []
    for f in p.index:
        try:
            ica = p.loc[f, ('ic_mean', 'ALL')]
            au = p.loc[f, ('auc', 'ALL')]
            sp = p.loc[f, ('spread10', 'ALL')]
            ic3 = [p.loc[f, ('ic_mean', q)] for q in ('TRAIN', 'VALID', 'OOS')]
        except KeyError:
            continue
        if not np.isfinite(ica) or not np.isfinite(au):
            continue
        ic3 = [x for x in ic3 if np.isfinite(x)]
        if len(ic3) < 3:
            continue
        cons = all(np.sign(x) == np.sign(ica) for x in ic3)
        rows.append(dict(feature=f, horizon=H, ic=ica, auc=au, cons=bool(cons), sp=sp))
    R = pd.DataFrame(rows)
    if R.empty:
        continue
    keep = R[R['cons'] & (R['ic'].abs() >= 0.008) & ((R['auc'] - 0.5).abs() >= 0.004)]
    keep = keep.sort_values('ic', key=lambda s: s.abs(), ascending=False)
    cnt = {}
    for _, r in keep.iterrows():
        fam = cand[(cand.feature == r['feature'])].family.iloc[0]
        if cnt.get(fam, 0) >= 2:
            continue
        cnt[fam] = cnt.get(fam, 0) + 1
        fin.append((r['feature'], H, float(r['ic']), float(r['auc']), fam))
        if sum(1 for x in fin if x[1] == H) >= 8:
            break
FINDF = pd.DataFrame(fin, columns=['feature', 'horizon', 'ic_all', 'auc_all', 'family'])
FINDF.to_csv(os.path.join(HERE, 'breakout_alpha_finalists.csv'), index=False,
             encoding='utf-8-sig')
# 同一特征只深检一次（取 IC 更强的 horizon）
FF = []
for _, r in FINDF.iterrows():
    if r['feature'] in [x[0] for x in FF]:
        continue
    FF.append((r['feature'], int(r['horizon'])))
print('finalists:')
print(FINDF.to_string(index=False), flush=True)
json.dump([{'feature': f, 'horizon': H} for f, H in FF],
          open(os.path.join(OUT, 'brk_finalists.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)

# 预取信号列
SIGV = {}
for f, _ in FF:
    SIGV[f] = df[f].values.astype(np.float64)

WF = []
GRID = []
CF = []
REGM = []
STAB = []
COST = []
for f, H in FF:
    sig = SIGV[f]
    ic, sp10, sp20, auc, t10, b10, nn = daily_eval(sig, H)
    # ── §24 Walk Forward（据实缩减窗口；2018-2020 无数据）
    wins = [('W1', 20210101, 20221231, 20230101, 20231231),
            ('W2', 20220101, 20231231, 20240101, 20241231),
            ('W3', 20230101, 20241231, 20250101, 20251231),
            ('W4', 20240101, 20251231, 20260101, 20260930)]
    for wn, a0, a1, b0, b1 in wins:
        sel = (day_date >= b0) & (day_date <= b1)
        s = summ(sp10, sel)
        si = summ(ic, sel)
        WF.append({'feature': f, 'horizon': H, 'window': wn,
                   'fit': '%d-%d' % (a0, a1), 'test': '%d-%d' % (b0, b1),
                   'n_days': s['n'], 'ic_mean': si['mean'], 'spread10': s['mean'],
                   'spread10_med': s['med'], 'spread10_pos': s['pos'],
                   'mean_ret_top10': summ(t10, sel)['mean'],
                   'wr_top10': np.nan, 'auc': summ(auc, sel)['mean']})
    # ── §25-a 选品分位网格
    Vsig = BV & np.isfinite(sig)
    _ndrow = np.repeat(aggd(Vsig.astype(float)), DPD)
    _rs = rank_day(sig, Vsig)
    for q in (0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50):
        top = Vsig & (_rs >= _ndrow * (1 - q)) & np.isfinite(Y[H])
        bot = Vsig & (_rs <= _ndrow * q) & np.isfinite(Y[H])
        yy = Y[H]
        d_top = sdv(aggd(np.where(top, yy, 0.0)), aggd(top.astype(float)))
        d_bot = sdv(aggd(np.where(bot, yy, 0.0)), aggd(bot.astype(float)))
        s = summ(d_top - d_bot)
        GRID.append({'grid_type': 'quantile', 'feature': f, 'horizon': H,
                     'param_name': 'q', 'param_value': q,
                     'n_days': s['n'], 'ic_mean': np.nan, 'top_ret': summ(d_top)['mean'],
                     'bot_ret': summ(d_bot)['mean'], 'spread': s['mean'], 'spread_pos': s['pos'],
                     'n_rows': int(top.sum() + bot.sum())})
    # ── §25-b 压力位窗口网格（以 Distance_to_R{N} 为 alpha，检验是否存在稳定区域）
    for N in (10, 15, 20, 30, 40, 60):
        cn = 'Distance_to_R%d' % N
        if cn not in df.columns:
            continue
        v = -df[cn].values.astype(np.float64)
        icN, spN, _, aucN, _, _, _ = daily_eval(v, H)
        s = summ(spN)
        GRID.append({'grid_type': 'resistance_N', 'feature': cn, 'horizon': H,
                     'param_name': 'N', 'param_value': N, 'n_days': s['n'],
                     'ic_mean': summ(icN)['mean'], 'top_ret': np.nan, 'bot_ret': np.nan,
                     'spread': s['mean'], 'spread_pos': s['pos'], 'n_rows': int(m),
                     'auc': summ(aucN)['mean']})
    # ── §25-c VR 阈值网格（任务书示例 0.7~1.6）
    for col, lab in (('Vol_over_MA20', 'VR20'), ('VR5', 'VR5')):
        if col not in df.columns:
            continue
        v = df[col].values.astype(np.float64)
        for thr in (0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6):
            sel = (v >= thr) & BV & np.isfinite(v)
            nd = int(np.unique(dayidx[sel]).size) if sel.any() else 0
            s = summ(np.where(sel, Y[H], np.nan))
            GRID.append({'grid_type': 'vr_threshold', 'feature': lab, 'horizon': H,
                         'param_name': 'threshold', 'param_value': thr, 'n_days': nd,
                         'ic_mean': np.nan, 'top_ret': s['mean'], 'bot_ret': np.nan,
                         'spread': np.nan, 'spread_pos': np.nan, 'n_rows': int(sel.sum())})
    # ── §29 Regime
    dirs = {}
    for r in ('BULL', 'NORMAL', 'BEAR'):
        sel = (reg_day == r)
        si = summ(ic, sel); ss = summ(sp10, sel)
        dirs[r] = np.sign(si['mean']) if np.isfinite(si['mean']) else 0
        REGM.append({'feature': f, 'horizon': H, 'regime': r, 'n_days': si['n'],
                     'ic_mean': si['mean'], 'ic_pos': si['pos'],
                     'spread10': ss['mean'], 'spread10_med': ss['med'],
                     'spread10_pos': ss['pos'], 'auc': summ(auc, sel)['mean'],
                     'top10_ret': summ(t10, sel)['mean']})
    rev = len(set([v for v in dirs.values() if v != 0])) > 1
    REGM.append({'feature': f, 'horizon': H, 'regime': 'DIRECTION_CHECK',
                 'n_days': 0, 'ic_mean': np.nan, 'ic_pos': np.nan, 'spread10': np.nan,
                 'spread10_med': np.nan, 'spread10_pos': np.nan, 'auc': np.nan,
                 'top10_ret': np.nan, 'regime_reversed': bool(rev)})
    # ── §27 成本（负向因子的可交易方向 = 做多低值组；双边成本 cost_of(slip)）
    dirsign = -1.0 if np.nanmean(sp10) < 0 else 1.0
    longv = b10 if dirsign < 0 else t10
    lg = summ(longv)
    for sl in SLIPS:
        c = cost_of(sl)
        COST.append({'feature': f, 'horizon': H, 'slip': sl, 'cost_roundtrip': c,
                     'trade_dir': ('long_low' if dirsign < 0 else 'long_high'),
                     'long_gross': lg['mean'], 'long_net': lg['mean'] - c,
                     'long_winrate': lg['pos'], 'n_days': lg['n'],
                     'spread_gross_abs': abs(summ(sp10)['mean']),
                     'spread_net_abs': abs(summ(sp10)['mean']) - 2 * c})
    # ── §28 时间稳定性（正比例 + 相对全样本方向的符号一致性）
    mkey = (day_date // 100)
    ykey = (day_date // 10000)
    _msp = np.nanmean(sp10)
    allsign = np.sign(_msp) if np.isfinite(_msp) else 0
    for lab, key in (('month', mkey), ('quarter', (ykey * 10 + (mkey % 100 - 1) // 3)), ('year', ykey)):
        g = pd.DataFrame({'k': key, 's': sp10, 'i': ic}).dropna(subset=['s'])
        if g.empty:
            continue
        a = g.groupby('k')['s'].agg(['mean', 'count'])
        b = g.groupby('k')['i'].mean()
        tot = a.loc[a['mean'] > 0, 'mean'].sum()
        top_share = (a['mean'].max() / tot) if tot > 0 else np.nan
        same = (np.sign(a['mean'].values) == allsign) if allsign != 0 else np.zeros(len(a), bool)
        same_ic = (np.sign(b.values) == allsign) if allsign != 0 else np.zeros(len(b), bool)
        STAB.append({'feature': f, 'horizon': H, 'freq': lab, 'n_buckets': int(a.shape[0]),
                     'positive_ratio': float((a['mean'] > 0).mean()),
                     'sign_consistency': float(same.mean()), 'n_same_sign': int(same.sum()),
                     'ic_positive_ratio': float((b > 0).mean()),
                     'ic_sign_consistency': float(same_ic.mean()),
                     'best_bucket': int(a['mean'].idxmax()),
                     'best_share_of_positive': float(top_share) if np.isfinite(top_share) else np.nan,
                     'mean_spread': float(a['mean'].mean())})

pd.DataFrame(WF).to_csv(os.path.join(HERE, 'breakout_alpha_walkforward.csv'),
                        index=False, encoding='utf-8-sig')
pd.DataFrame(GRID).to_csv(os.path.join(HERE, 'breakout_alpha_parameter_grid.csv'),
                          index=False, encoding='utf-8-sig')
pd.DataFrame(REGM).to_csv(os.path.join(HERE, 'breakout_alpha_regime.csv'),
                          index=False, encoding='utf-8-sig')
pd.DataFrame(COST).to_csv(os.path.join(HERE, 'breakout_alpha_cost.csv'),
                          index=False, encoding='utf-8-sig')
pd.DataFrame(STAB).to_csv(os.path.join(HERE, 'breakout_alpha_stability.csv'),
                          index=False, encoding='utf-8-sig')

# ══════════════ §26 Counterfactual（匹配：日期/行业/市值/流动性/历史涨幅/距压力位）
print('Counterfactual ...', flush=True)
ind = pd.factorize(df['industry'].astype(str))[0]
ind[ind < 0] = 0


def dec(v, mask):
    r = rank_day(np.nan_to_num(np.asarray(v, dtype=np.float64), nan=0.0), mask)
    nd = np.repeat(aggd(mask.astype(float)), DPD)
    p = sdv(r, nd)
    return np.clip(np.floor(np.nan_to_num(p, nan=0.0) * 10).astype(np.int64), 0, 9)


VOK = BV & np.isfinite(df['LogMV'].values) & np.isfinite(df['LogAmount'].values) \
      & np.isfinite(df['Ret_20'].values) & np.isfinite(df['Distance_to_R20'].values)
DEC = {}
for cn in ('LogMV', 'LogAmount', 'Ret_20', 'Distance_to_R20'):
    DEC[cn] = dec(df[cn].values, VOK)
KEY = ((((ind.astype(np.int64) * 10 + DEC['LogMV']) * 10 + DEC['LogAmount']) * 10
        + DEC['Ret_20']) * 10 + DEC['Distance_to_R20'])
for f, H in FF:
    sig = SIGV[f]
    V = BV & np.isfinite(sig)
    ndrow = np.repeat(aggd(V.astype(float)), DPD)
    rs = rank_day(sig, V)
    pct = sdv(rs, ndrow)
    hi = V & (pct >= 0.8)
    lo = V & (pct <= 0.2)
    yy = Y[H]
    g = pd.DataFrame({'d': dayidx, 'k': KEY, 'hi': hi.astype(float), 'lo': lo.astype(float),
                      'yh': np.where(hi, yy, 0.0), 'yl': np.where(lo, yy, 0.0)})
    ag = g.groupby(['d', 'k'], sort=False)[['hi', 'lo', 'yh', 'yl']].sum()
    ag = ag[(ag['hi'] > 0) & (ag['lo'] > 0)]
    ag['diff'] = ag['yh'] / ag['hi'] - ag['yl'] / ag['lo']
    ag['w'] = np.minimum(ag['hi'], ag['lo'])
    _t = ag.reset_index()
    _t['dw'] = _t['diff'] * _t['w']
    _gg = _t.groupby('d')[['dw', 'w']].sum()
    dayd = (_gg['dw'] / _gg['w']).dropna()
    raw_hi = summ(yy, hi)['mean']
    raw_lo = summ(yy, lo)['mean']
    raw_d = summ(np.where(hi, yy, np.nan))['mean'] - summ(np.where(lo, yy, np.nan))['mean']
    mst = summ(dayd.values)
    matched_rows = int(ag['hi'].sum() + ag['lo'].sum())
    cov = matched_rows / max(hi.sum() + lo.sum(), 1)
    # 判定必须按「方向」而非绝对符号：负向因子的 raw_diff<0，matched 同号且量级保留才算 SURVIVES
    if cov < 0.05 or mst['n'] < 20 or not np.isfinite(mst['mean']) or not np.isfinite(raw_d):
        verdict = 'INSUFFICIENT_MATCH'
    elif np.sign(mst['mean']) != np.sign(raw_d):
        verdict = 'CONFOUNDING'
    elif abs(mst['mean']) < abs(raw_d) * 0.5:
        verdict = 'WEAKENED'
    else:
        verdict = 'SURVIVES'
    CF.append({'feature': f, 'horizon': H,
               'raw_hi': raw_hi, 'raw_lo': raw_lo, 'raw_diff': raw_d,
               'matched_diff': mst['mean'], 'matched_days': mst['n'],
               'matched_t': mst['t'], 'matched_pos': mst['pos'],
               'matched_rows': matched_rows, 'match_coverage': cov,
               'verdict': verdict})
pd.DataFrame(CF).to_csv(os.path.join(HERE, 'breakout_alpha_counterfactual.csv'),
                        index=False, encoding='utf-8-sig')

# ══════════════ §22 二阶交互（仅 6 组经济含义组合）
print('Interaction ...', flush=True)
PAIRS = [('Ret_20', 'RelativeStrength_20', 'Trend x RelativeStrength'),
         ('Ret_20', 'Vol_over_MA20', 'Trend x Volume'),
         ('Distance_to_R20', 'Vol_over_MA20', 'Distance x Volume'),
         ('ATR20_pct', 'Vol_over_MA20', 'Volatility x Volume'),
         ('RelativeStrength_20', 'Industry_Return_20', 'RelativeStrength x IndustryStrength'),
         ('Efficiency_20', 'Turnover_20', 'PriceEfficiency x Turnover')]
INT = []
for a, b, lab in PAIRS:
    if a not in df.columns or b not in df.columns:
        continue
    va = df[a].values.astype(np.float64)
    vb = df[b].values.astype(np.float64)
    Vp = BV & np.isfinite(va) & np.isfinite(vb)
    ndrow = np.repeat(aggd(Vp.astype(float)), DPD)
    ra = sdv(rank_day(va, Vp), ndrow) - 0.5
    rb = sdv(rank_day(vb, Vp), ndrow) - 0.5
    prod = np.where(Vp, ra * rb, np.nan)
    for H in HOR:
        ic, sp10, sp20, auc, t10, b10, _ = daily_eval(prod, H)
        s = summ(sp10); si = summ(ic)
        INT.append({'pair': lab, 'f1': a, 'f2': b, 'horizon': H,
                    'ic_mean': si['mean'], 'ic_t': si['t'], 'spread10': s['mean'],
                    'spread10_pos': s['pos'], 'auc': summ(auc)['mean'],
                    'top10_ret': summ(t10)['mean'], 'n_days': s['n']})
pd.DataFrame(INT).to_csv(os.path.join(HERE, 'breakout_alpha_interaction.csv'),
                         index=False, encoding='utf-8-sig')

# ══════════════ §34 Alpha Registry + §35 research.json（§30 Gate 1~13 逐条裁定）
print('Registry ...', flush=True)
FORMULA = {
    'ATR5_pct': 'ATR(TrueRange,5) / Close',
    'ATR10_pct': 'ATR(TrueRange,10) / Close',
    'Turnover_1': 'Turnover(t)',
    'Turnover_3': 'mean(Turnover, 3)',
    'K_BodyAbs20': 'mean20( |Close-Open| / prevClose )',
    'K_LargeBodyFreq20': 'mean20( |Close-Open|/prevClose > 0.05 )',
    'Dist_to_20D_Low': 'Close / min20(Low) - 1',
    'Volume_Shock': 'Volume / max60(Volume)',
    'Close_vs_MA60': 'Close / MA60 - 1',
    'Close_Position_60': '(Close - min60(Low)) / (max60(High) - min60(Low))',
}
EVENT_DEF = ('Universe(seq>=60 & 非ST/退市/BSE & amount>=5e4千元) ∩ 当日横截面上 '
             'min_{N in 10,20,40,60} Distance_to_R{N} 落于最低 10% 分位；'
             'R_N = max(High[t-N : t-1])（仅用 T 日及之前，不含当日）')
ENTRY_RULE = 'Signal = T 日收盘（特征仅用 <=T 数据）；Entry = T+1 开盘；停牌样本剔除'

W = pd.DataFrame(WF); G = pd.DataFrame(GRID); RG = pd.DataFrame(REGM)
CS = pd.DataFrame(COST); SB = pd.DataFrame(STAB); CFF = pd.DataFrame(CF)
BN = ['B1_AllCandidates', 'B2_RandomOrder', 'B3_Momentum_Ret20', 'B4_DistToResistance',
      'B5_Volume_VR20', 'B6_SimpleBreakoutFlag']


def _g(d, f, H, **kv):
    x = d[(d.feature == f) & (d.horizon == H)]
    for k, v in kv.items():
        x = x[x[k] == v]
    return x


def _num(v):
    try:
        return float(v)
    except Exception:
        return np.nan


REG = []
for i, (f, H) in enumerate(FF, 1):
    st = _S[(_S.feature == f) & (_S.horizon == H)].set_index('period')
    tr, va, oo, al = (_num(st.loc[p, 'ic_mean']) if p in st.index else np.nan
                      for p in ('TRAIN', 'VALID', 'OOS', 'ALL'))
    icir = _num(st.loc['ALL', 'icir']) if 'ALL' in st.index else np.nan
    sp = _num(st.loc['ALL', 'spread10']) if 'ALL' in st.index else np.nan
    auc = _num(st.loc['ALL', 'auc']) if 'ALL' in st.index else np.nan
    sdg = _num(st.loc['ALL', 'std_gap']) if 'ALL' in st.index else np.nan
    sg = int(np.sign(al)) if np.isfinite(al) else 0
    # ── Gate 1/2: 结构性前提（压力位 shift(1)、T+1 开盘入场，构建时已强制）
    gates = {'G1_no_lookahead': True, 'G2_tradable': True}
    gates['G3_train'] = bool(np.isfinite(tr) and np.sign(tr) == sg and abs(tr) >= 0.005)
    gates['G4_valid'] = bool(np.isfinite(va) and np.sign(va) == sg and abs(va) >= 0.005)
    gates['G5_oos'] = bool(np.isfinite(oo) and np.sign(oo) == sg and abs(oo) >= 0.005)
    gates['G6_ic_stable'] = bool(np.isfinite(icir) and abs(icir) >= 0.10)
    gates['G7_spread'] = bool(np.isfinite(sp) and abs(sp) >= 0.005)
    gates['G8_discrim'] = bool(np.isfinite(auc) and abs(auc - 0.5) >= 0.02
                               and np.isfinite(sdg) and abs(sdg) >= 0.10)
    gq = _g(G, f, H, grid_type='quantile')
    gates['G9_param'] = bool((not gq.empty) and (np.sign(gq['spread'].values) == sg).all())
    cf = _g(CFF, f, H)
    cfv = cf['verdict'].iloc[0] if not cf.empty else 'MISSING'
    gates['G10_counterfactual'] = bool(cfv == 'SURVIVES')
    sy = _g(SB, f, H, freq='year')
    sq = _g(SB, f, H, freq='quarter')
    ysc = _num(sy['sign_consistency'].iloc[0]) if not sy.empty else np.nan
    qsc = _num(sq['sign_consistency'].iloc[0]) if not sq.empty else np.nan
    ysh = _num(sy['best_share_of_positive'].iloc[0]) if not sy.empty else np.nan
    gates['G11_time_stable'] = bool(np.isfinite(ysc) and ysc >= 0.6
                                    and np.isfinite(qsc) and qsc >= 0.6)
    rg = _g(RG, f, H)
    rev = bool(rg['regime_reversed'].dropna().iloc[0]) if rg['regime_reversed'].notna().any() else True
    rsig = set(np.sign(rg[rg.regime.isin(['BULL', 'NORMAL', 'BEAR'])]['spread10'].dropna()))
    gates['G12_regime'] = bool((not rev) and len(rsig) == 1)
    c30 = _g(CS, f, H, slip=0.003)
    gates['G13_cost'] = bool((not c30.empty) and np.isfinite(c30['long_net'].iloc[0])
                             and c30['long_net'].iloc[0] > 0)
    # ── §31 硬性 FAIL 清单（命中任一 → 直接 FAIL，不再给 CONDITIONAL）
    HARD = ['G1_no_lookahead', 'G2_tradable', 'G5_oos', 'G6_ic_stable', 'G7_spread',
            'G8_discrim', 'G9_param', 'G11_time_stable', 'G12_regime', 'G13_cost']
    bad = [k for k in gates if not gates[k]]
    hard = [k for k in HARD if not gates[k]]
    if cfv == 'CONFOUNDING':
        hard.append('G10_counterfactual')
    status = 'PASS' if not bad else ('FAIL' if hard else 'CONDITIONAL')
    wf = _g(W, f, H)
    wfsig = sorted(set(np.sign(wf['spread10'].dropna())))
    sm = _g(SB, f, H, freq='month')
    cost = {}
    for lb, sl in (('cost_0', 0.0), ('cost_10bp', 0.001), ('cost_20bp', 0.002), ('cost_30bp', 0.003)):
        z = _g(CS, f, H, slip=sl)
        cost[lb] = _num(z['long_net'].iloc[0]) if not z.empty else np.nan
    # §21 与简单基准对比：|IC| 是否超过 B2~B6 中最强的那个
    _bl = S[(S.feature.isin(BN)) & (S.horizon == H) & (S.period == 'ALL')]['ic_mean'].abs()
    bmax = float(_bl.max()) if len(_bl) else np.nan
    REG.append({
        'alpha_id': 'BRK-%03d' % i, 'feature': f,
        'feature_family': _S[(_S.feature == f)].family.iloc[0],
        'formula': FORMULA.get(f, f), 'event_definition': EVENT_DEF, 'entry_rule': ENTRY_RULE,
        'horizon': 'T+%d' % H,
        'train_ic': tr, 'valid_ic': va, 'oos_ic': oo, 'ic_all': al, 'icir': icir,
        'top10_return': _num(st.loc['ALL', 'top10_ret']), 'bottom10_return': _num(st.loc['ALL', 'bot10_ret']),
        'top_bottom_spread': sp, 'spread20': _num(st.loc['ALL', 'spread20']),
        'winner_mean': _num(st.loc['ALL', 'win_mean']), 'loser_mean': _num(st.loc['ALL', 'los_mean']),
        'std_gap': sdg, 'effect_size': _num(st.loc['ALL', 'effect_size']), 'auc': auc,
        'rank_sep': _num(st.loc['ALL', 'rank_sep']), 'wr_top10': _num(st.loc['ALL', 'wr_top10']),
        'month_sign_consistency': _num(sm['sign_consistency'].iloc[0]) if not sm.empty else np.nan,
        'month_positive_ratio': _num(sm['positive_ratio'].iloc[0]) if not sm.empty else np.nan,
        'year_sign_consistency': ysc, 'quarter_sign_consistency': qsc,
        'year_positive_ratio': _num(sy['positive_ratio'].iloc[0]) if not sy.empty else np.nan,
        'best_share_of_positive': ysh,
        'regime_stability': ('CONSISTENT' if gates['G12_regime'] else
                             ('REVERSED' if rev else 'MIXED')),
        'wf_spread_signs': str(wfsig), 'wf_ic_consistent': bool(len(set(np.sign(wf['ic_mean'].dropna()))) == 1),
        'cost_0': cost['cost_0'], 'cost_10bp': cost['cost_10bp'],
        'cost_20bp': cost['cost_20bp'], 'cost_30bp': cost['cost_30bp'],
        'parameter_stability': ('CONTINUOUS' if gates['G9_param'] else 'SINGLE_POINT'),
        'counterfactual_result': cfv,
        'counterfactual_coverage': _num(cf['match_coverage'].iloc[0]) if not cf.empty else np.nan,
        'baseline_max_abs_ic': bmax, 'beats_baseline': bool(np.isfinite(bmax) and abs(al) > bmax),
        'gates_passed_list': ';'.join([k for k in gates if gates[k]]),
        'gates_failed': ';'.join(bad), 'hard_fail': ';'.join(hard), 'n_gates_failed': len(bad),
        'status': status, 'fail_reason': ('NONE' if not bad else ';'.join(bad))})
REGD = pd.DataFrame(REG)
REGD.to_csv(os.path.join(HERE, 'breakout_alpha_registry.csv'), index=False, encoding='utf-8-sig')
print(REGD[['alpha_id', 'feature', 'horizon', 'ic_all', 'oos_ic', 'top_bottom_spread', 'auc',
            'n_gates_failed', 'status']].to_string(index=False), flush=True)

# ── 汇总总体判定
_npass = int((REGD.status == 'PASS').sum())
_ncond = int((REGD.status == 'CONDITIONAL').sum())
OVERALL = 'PASS' if _npass > 0 else ('CONDITIONAL' if _ncond > 0 else 'FAIL')
bl = {}
for h in (3, 5):
    bl['T+%d' % h] = S[(S.feature.isin(BN)) & (S.horizon == h) & (S.period == 'ALL')][
        ['feature', 'ic_mean', 'auc', 'icir', 'spread10', 'n_days']].to_dict('records')
MET = json.load(open(os.path.join(OUT, 'brk_meta.json'), encoding='utf-8'))
json.dump({
    'research_question': '突破前可观察的量价/波动/趋势/相对强弱/流动性信息，能否在横截面上'
                         '区分未来 T+3 / T+5 的强者与弱者？',
    'dataset': MET['pool'], 'window': MET['window'],
    'features_tested': len(MET['features']),
    'periods': {'TRAIN': '20210401-20231231', 'VALID': '20240101-20251231', 'OOS': '20260101-20260924'},
    'entry_anchor': ENTRY_RULE, 'event_definition': EVENT_DEF, 'trans_cost': 'FEE=0.00055 + 2*slip',
    'baseline': bl,
    'overall_verdict': OVERALL,
    'n_pass': _npass, 'n_conditional': _ncond, 'n_finalists': len(REGD),
    'registry': REGD.to_dict('records'),
}, open(os.path.join(HERE, 'breakout_alpha_research.json'), 'w', encoding='utf-8'),
    ensure_ascii=False, indent=1, default=float)

print('完成', flush=True)
print(pd.DataFrame(CF).to_string(index=False))
print(pd.DataFrame(INT).to_string(index=False))
print(pd.DataFrame(WF).to_string(index=False))
print('\n=== 总体判定: %s (PASS=%d CONDITIONAL=%d / %d) ===' % (OVERALL, _npass, _ncond, len(REGD)),
      flush=True)
