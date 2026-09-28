# -*- coding: utf-8 -*-
"""hep_engine: H-EARN-POST 分析引擎（共享原语）

隔离声明（§1）：本模块只读 data/hep_events.parquet 与市场级 Tushare cache，
不 import 亦不调用 ARCHIVE / HVT / DLG / F120 / Theme Quant / te_buy_pool /
突破策略 / 天量策略 / 首板策略 的任何代码、信号或筛选结果。

横截面（cohort）= 同一决策日 sig_k（= E 的日历索引）。所有 IC / 分位 / 中性化
均在 cohort 内部完成，只使用 E 收盘及之前可得的信息。

本文件同时保留慢速逐 cohort 版本（*_loop）用于与向量化版本（默认名）做一致性
自检，见 hep_analyze.selftest()。
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hep_common import (DATA, PREREG, ENTRIES, HORIZONS, MIN_XS, WINSOR,
                        zs, rank_pct, spearman_ic, auc_score, ols_resid,
                        ic_stats, dummies)

MOM_COLS = ['mom_5', 'mom_10', 'mom_20', 'mom_60']
VAL_COLS = ['pe_ttm', 'pb', 'ps_ttm', 'dv_ttm']       # EV_EBITDA / PEG 缓存中不可得
SIZE_COLS = ['log_mv', 'log_cmv']
LIQ_COLS = ['turnover', 'log_amt']
IND_COL = 'ind_l1'                                    # §18 申万一级（主口径）
RET_COL = 'ex_%d'                                     # §2 预测目标：相对沪深300 超额收益

# 四个候选信号（已在 hep_build 用 TRAIN 期方向冻结，_d 后缀 = 高即好）
SIG_COLS = {
    'SIG_FSC': 'fsc_z_d',
    'SIG_RESID': 'sig_resid_d',
    'SIG_QUADA': 'sig_quada_d',
    'SIG_INTER': 'sig_inter_d',
}
BASE_COLS = ['B1_MOM', 'B2_VAL', 'B3_GROW', 'B4_REACT', 'B5_FM']


# ------------------------------------------------------------------ 载入 / 特征
def load():
    return pd.read_parquet(os.path.join(DATA, 'hep_events.parquet'))


def add_features(d):
    """规模/流动性取对数 + 逐 cohort z 标准化 + §23 基线合成。"""
    d = d.copy()
    d['log_mv'] = np.log(pd.to_numeric(d['total_mv'], errors='coerce').clip(lower=1.0))
    d['log_cmv'] = np.log(pd.to_numeric(d['circ_mv'], errors='coerce').clip(lower=1.0))
    d['log_amt'] = np.log(pd.to_numeric(d['amt20'], errors='coerce').clip(lower=1.0))
    for c in ('pe_ttm', 'pb', 'ps_ttm', 'dv_ttm', 'turnover'):
        d[c] = pd.to_numeric(d[c], errors='coerce')
    raw = {'m5': 'mom_5', 'm10': 'mom_10', 'm20': 'mom_20', 'm60': 'mom_60',
           'pe': 'pe_ttm', 'pb': 'pb', 'ps': 'ps_ttm', 'dv': 'dv_ttm',
           'mv': 'log_mv', 'cmv': 'log_cmv', 'to': 'turnover', 'amt': 'log_amt',
           'np': 'np_yoy', 'rev': 'rev_yoy', 'roe': 'roe',
           'react': 'px_ret_rel'}
    tmp = pd.DataFrame(index=d.index)
    for key, col in raw.items():
        tmp['z_' + key] = pd.to_numeric(d[col], errors='coerce')
    for idx in d.groupby('sig_k', sort=False).groups.values():
        sub = tmp.loc[idx]
        for key in raw:
            tmp.loc[idx, 'z_' + key] = zs(sub['z_' + key]).values
    Z = {k: tmp['z_' + k] for k in raw}
    # B1 纯 Momentum
    d['B1_MOM'] = pd.concat([Z['m5'], Z['m20'], Z['m60']], axis=1).mean(axis=1)
    # B2 纯 Value（PE/PB/PS 反向 = 便宜，股息率正向）
    d['B2_VAL'] = pd.concat([-Z['pe'], -Z['pb'], -Z['ps'], Z['dv']], axis=1).mean(axis=1)
    # B3 纯 Fundamental Growth
    d['B3_GROW'] = pd.concat([Z['np'], Z['rev'], Z['roe']], axis=1).mean(axis=1)
    # B4 纯 Post-Earnings Price Reaction（原始价格反应，方向由 TRAIN 决定）
    d['B4_REACT'] = Z['react']
    # B5 Fundamental + Momentum 简单组合
    d['B5_FM'] = pd.concat([d['B1_MOM'], d['B3_GROW']], axis=1).mean(axis=1)
    # §13/§14 候选信号（方向已冻结）
    for nm, col in SIG_COLS.items():
        d[nm] = pd.to_numeric(d[col], errors='coerce')
    d['CTRL_MOM'] = pd.concat([Z['m5'], Z['m10'], Z['m20'], Z['m60']], axis=1).mean(axis=1)
    d['CTRL_VAL'] = d['B2_VAL']
    d['CTRL_SIZE'] = pd.concat([Z['mv'], Z['cmv']], axis=1).mean(axis=1)
    d['CTRL_LIQ'] = pd.concat([Z['to'], Z['amt']], axis=1).mean(axis=1)
    return d


# ------------------------------------------------------------------ 方向冻结
def freeze_dir(d, facs, train_years, horizon=20):
    """§40 方向只允许用 TRAIN 期确定，冻结后用于全样本（禁止全样本调参）。"""
    tr = d[d['rep_year'].between(train_years[0], train_years[1])]
    out = {}
    for f in facs:
        ic = spearman_ic(tr[f].values, tr[RET_COL % horizon].values)
        out[f] = 1.0 if (np.isfinite(ic) and ic >= 0) else -1.0
    return out


def apply_dir(d, facs, dirs):
    for f in facs:
        d[f] = pd.to_numeric(d[f], errors='coerce') * dirs[f]
    return d


# ------------------------------------------------------------------ 横截面基元
def _prep(d, sig, ret, min_xs):
    s = pd.DataFrame({
        'k': np.asarray(d['sig_k']),
        's': pd.to_numeric(d[sig], errors='coerce').astype('float64').to_numpy(),
        'r': pd.to_numeric(d[ret], errors='coerce').astype('float64').to_numpy(),
    }).dropna()
    if s.empty:
        return None
    cnt = s.groupby('k', sort=True)['s'].transform('size')
    s = s[cnt >= min_xs]
    return s if not s.empty else None


def cohorts(d):
    """预计算 cohort 的**位置**索引数组（供 neutralize 复用，避免重复 groupby）。"""
    pos = pd.Series(np.arange(len(d)), index=d.index)
    return [pos.loc[ix].to_numpy() for ix in d.groupby('sig_k', sort=False).groups.values()]


# ------------------------------------------------------------------ Rank IC
def ic_series(d, sig, ret, min_xs=MIN_XS):
    """逐 cohort Spearman Rank IC（向量化），返回索引 = sig_k 的 Series。"""
    s = _prep(d, sig, ret, min_xs)
    if s is None:
        return pd.Series(dtype=float)
    k = s['k'].values
    g = s.groupby('k', sort=True)
    rx = g['s'].rank()
    ry = g['r'].rank()
    mx = rx.groupby(k).transform('mean')
    my = ry.groupby(k).transform('mean')
    dx, dy = rx - mx, ry - my
    cxy = (dx * dy).groupby(k).transform('sum')
    vx = (dx * dx).groupby(k).transform('sum')
    vy = (dy * dy).groupby(k).transform('sum')
    den = np.sqrt(vx * vy)
    with np.errstate(invalid='ignore', divide='ignore'):
        ic = np.where(den > 1e-12, cxy / den, np.nan)
    out = pd.DataFrame({'k': k, 'ic': ic}).groupby('k', sort=True)['ic'].first()
    return out.dropna()


def ic_series_loop(d, sig, ret, min_xs=MIN_XS):
    out = {}
    for k, idx in d.groupby('sig_k', sort=True).groups.items():
        s = d.loc[idx]
        ic = spearman_ic(s[sig].values, s[ret].values)
        if np.isfinite(ic):
            out[k] = ic
    return pd.Series(out)


def ic_row(ics, horizon):
    return ic_stats(ics.values, horizon)


# ------------------------------------------------------------------ 分位组合
def bucket_means(d, sig, ret, q=0.2, min_xs=MIN_XS):
    """逐 cohort Top/Bottom q 分位等权收益（向量化）。"""
    s = _prep(d, sig, ret, min_xs)
    if s is None:
        return pd.Series(dtype=float), pd.Series(dtype=float), pd.Series(dtype=float)
    g = s.groupby('k', sort=True)['s']
    hi = s['k'].map(g.quantile(1.0 - q))
    lo = s['k'].map(g.quantile(q))
    top = s.loc[s['s'] >= hi].groupby('k')['r'].mean()
    bot = s.loc[s['s'] <= lo].groupby('k')['r'].mean()
    n = s.groupby('k')['r'].size()
    idx = top.index.intersection(bot.index)
    return top.reindex(idx), bot.reindex(idx), n.reindex(idx)


def bucket_means_loop(d, sig, ret, q=0.2, min_xs=MIN_XS):
    top, bot, n = {}, {}, {}
    for k, idx in d.groupby('sig_k', sort=True).groups.items():
        s = pd.DataFrame({'s': d.loc[idx, sig].values,
                          'r': d.loc[idx, ret].values}).dropna()
        if len(s) < min_xs:
            continue
        lo, hi = s['s'].quantile(q), s['s'].quantile(1 - q)
        t = s[s['s'] >= hi]['r'].mean()
        b = s[s['s'] <= lo]['r'].mean()
        if np.isfinite(t) and np.isfinite(b):
            top[k], bot[k], n[k] = t, b, len(s)
    return pd.Series(top), pd.Series(bot), pd.Series(n)


def quintile_means(d, sig, ret, min_xs=MIN_XS):
    """§21 逐 cohort [t10, t20, mid, b20, b10] 等权收益（T10/B10=q0.1，T20/B20=q0.2）。"""
    s = _prep(d, sig, ret, min_xs)
    if s is None:
        return pd.DataFrame()
    g = s.groupby('k', sort=True)['s']
    q10 = s['k'].map(g.quantile(0.10))
    q20 = s['k'].map(g.quantile(0.20))
    q80 = s['k'].map(g.quantile(0.80))
    q90 = s['k'].map(g.quantile(0.90))
    out = pd.DataFrame({
        't10': s.loc[s['s'] >= q90].groupby('k')['r'].mean(),
        't20': s.loc[s['s'] >= q80].groupby('k')['r'].mean(),
        'mid': s.loc[(s['s'] > q20) & (s['s'] < q80)].groupby('k')['r'].mean(),
        'b20': s.loc[s['s'] <= q20].groupby('k')['r'].mean(),
        'b10': s.loc[s['s'] <= q10].groupby('k')['r'].mean(),
    })
    out['n'] = s.groupby('k')['r'].size()
    return out.dropna(subset=['t10', 't20', 'mid', 'b20', 'b10'])


def spread_row(d, sig, ret, q=0.2, cost_bps=30.0, min_xs=MIN_XS):
    """§21 Top-Bottom 毛/净价差。净额口径：多空各换手一次 => 4c；纯多头 => 2c。"""
    t, b, n = bucket_means(d, sig, ret, q, min_xs)
    if len(t) == 0:
        return dict(n_day=0, top=np.nan, bot=np.nan, ls_gross=np.nan,
                    ls_net=np.nan, top_net=np.nan, turn=np.nan)
    diff = (t - b).dropna()
    c = cost_bps / 1e4
    return dict(n_day=int(len(diff)), top=float(t.mean()), bot=float(b.mean()),
                ls_gross=float(diff.mean()), ls_net=float(diff.mean() - 4 * c),
                top_net=float(t.mean() - 2 * c),
                turn=float((diff > 0).mean()))


# ------------------------------------------------------------------ Winner/Loser 判别
def auc_block(d, sig, ret, win_q=0.2, loss_q=0.2, min_xs=MIN_XS):
    """§22 Winner = cohort 内未来收益前 win_q，Loser = 后 loss_q。"""
    s = _prep(d, sig, ret, min_xs)
    if s is None:
        return dict(n=0, auc=np.nan, eff=np.nan, std_gap=np.nan,
                    rank_sep=np.nan, prec10=np.nan)
    g = s.groupby('k', sort=True)['r']
    hi = s['k'].map(g.quantile(1.0 - win_q))
    lo = s['k'].map(g.quantile(loss_q))
    r = s['r'].values
    y = np.where(r >= hi.values, 1.0, np.where(r <= lo.values, 0.0, np.nan))
    f = pd.DataFrame({'k': s['k'].values, 's': s['s'].values, 'y': y})
    f['pct'] = f.groupby('k')['s'].rank(pct=True)
    nn = f.dropna(subset=['y']).groupby('k')['y'].size()
    keep = nn[nn >= 2 * min_xs].index
    f = f[f['k'].isin(keep)].dropna(subset=['y'])
    if f.empty:
        return dict(n=0, auc=np.nan, eff=np.nan, std_gap=np.nan,
                    rank_sep=np.nan, prec10=np.nan)
    auc = auc_score(f['s'].values, f['y'].values)
    sw, sl = f[f['y'] == 1]['s'], f[f['y'] == 0]['s']
    sd = f['s'].std(ddof=1)
    eff = (sw.mean() - sl.mean()) / sd if sd > 1e-12 else np.nan
    std_gap = sw.std(ddof=1) - sl.std(ddof=1)
    rank_sep = f[f['y'] == 1]['pct'].mean() - f[f['y'] == 0]['pct'].mean()
    t10 = f[f['pct'] >= 0.9]
    prec10 = t10['y'].mean() if len(t10) else np.nan
    return dict(n=int(len(f)), auc=float(auc), eff=float(eff),
                std_gap=float(std_gap), rank_sep=float(rank_sep),
                prec10=float(prec10) if np.isfinite(prec10) else np.nan)


def auc_block_loop(d, sig, ret, win_q=0.2, loss_q=0.2, min_xs=MIN_XS):
    W = []
    for k, idx in d.groupby('sig_k', sort=True).groups.items():
        s = pd.DataFrame({'s': d.loc[idx, sig].values,
                          'r': d.loc[idx, ret].values}).dropna()
        if len(s) < min_xs:
            continue
        hi, lo = s['r'].quantile(1 - win_q), s['r'].quantile(loss_q)
        s = s.assign(y=np.where(s['r'] >= hi, 1.0, np.where(s['r'] <= lo, 0.0, np.nan)),
                     pct=s['s'].rank(pct=True))
        s = s.dropna(subset=['y'])
        if len(s) >= 2 * min_xs:
            W.append(s)
    if not W:
        return dict(n=0, auc=np.nan, eff=np.nan, std_gap=np.nan,
                    rank_sep=np.nan, prec10=np.nan)
    a = pd.concat(W, ignore_index=True)
    auc = auc_score(a['s'].values, a['y'].values)
    sw, sl = a[a['y'] == 1]['s'], a[a['y'] == 0]['s']
    sd = a['s'].std(ddof=1)
    eff = (sw.mean() - sl.mean()) / sd if sd > 1e-12 else np.nan
    std_gap = sw.std(ddof=1) - sl.std(ddof=1)
    rank_sep = a[a['y'] == 1]['pct'].mean() - a[a['y'] == 0]['pct'].mean()
    t10 = a[a['pct'] >= 0.9]
    prec10 = t10['y'].mean() if len(t10) else np.nan
    return dict(n=int(len(a)), auc=float(auc), eff=float(eff),
                std_gap=float(std_gap), rank_sep=float(rank_sep),
                prec10=float(prec10) if np.isfinite(prec10) else np.nan)


# ------------------------------------------------------------------ 中性化
def _ind_reduced(series, ind_min_n):
    """行业内样本数 < ind_min_n 的全部并入 'OTHER'，避免每 cohort 上百个哑变量过拟合。"""
    s = pd.Series(series).astype(str).fillna('NA')
    vc = s.value_counts()
    keep = set(vc[vc >= ind_min_n].index)
    return s.where(s.isin(keep), 'OTHER')


def _ctrl_matrix(d, cols, ind_col=None, ind_min_n=5):
    X = d[cols].apply(pd.to_numeric, errors='coerce').values.astype(float)
    parts = [X]
    if ind_col is not None and ind_col in d.columns:
        parts.append(dummies(_ind_reduced(d[ind_col].values, ind_min_n).values))
    return np.column_stack(parts) if parts else X


def neutralize(d, sig, ctrl_cols, ind_col=None, min_xs=MIN_XS, ind_min_n=5, groups=None):
    """逐 cohort 对 ctrl_cols（及可选行业哑变量）回归，返回残差 Series（索引与 d 对齐）。

    groups 为 cohorts(d) 预计算的位置索引；控制变量以 numpy 数组切片参与回归，
    避免逐 cohort 构造 DataFrame。
    """
    out = pd.Series(np.nan, index=d.index, dtype=float)
    if groups is None:
        groups = cohorts(d)
    y = pd.to_numeric(d[sig], errors='coerce').astype('float64').to_numpy()
    C = [pd.to_numeric(d[c], errors='coerce').astype('float64').to_numpy()
         for c in ctrl_cols]
    iv = None
    if ind_col is not None and ind_col in d.columns:
        iv = d[ind_col].astype(str).to_numpy()
    for pos in groups:
        parts = []
        if C:
            parts.append(np.column_stack([c[pos] for c in C]))
        if iv is not None:
            parts.append(dummies(_ind_reduced(iv[pos], ind_min_n)))
        X = np.column_stack(parts) if parts else np.zeros((len(pos), 0))
        if len(pos) < max(min_xs, X.shape[1] + 6):
            continue
        out.iloc[pos] = ols_resid(y[pos], X)
    return out


def neutralized_ic(d, sig, ret, ctrl_cols, ind_col=None, min_xs=MIN_XS, ind_min_n=5, groups=None):
    """先逐 cohort 中性化信号，再计算 IC（等价于横截面偏相关）。"""
    r = neutralize(d, sig, ctrl_cols, ind_col, min_xs, ind_min_n, groups)
    return ic_series(d.assign(_resid=r), '_resid', ret, min_xs)


# ------------------------------------------------------------------ 绩效
def perf_daily(r, freq=252):
    r = pd.Series(r, dtype=float).dropna()
    if len(r) < 20:
        return dict(days=len(r), ann=np.nan, vol=np.nan, sharpe=np.nan, mdd=np.nan)
    nav = (1.0 + r).cumprod()
    yrs = len(r) / float(freq)
    ann = nav.iloc[-1] ** (1.0 / yrs) - 1.0 if yrs > 0 else np.nan
    vol = r.std(ddof=1) * np.sqrt(freq)
    sharpe = ann / vol if vol > 1e-12 else np.nan
    mdd = float((nav / nav.cummax() - 1.0).min())
    return dict(days=int(len(r)), ann=float(ann), vol=float(vol),
                sharpe=float(sharpe), mdd=mdd)


def info_ratio(ex):
    ex = pd.Series(ex, dtype=float).dropna()
    if len(ex) < 20:
        return np.nan
    sd = ex.std(ddof=1)
    return float(ex.mean() / sd * np.sqrt(252)) if sd > 1e-12 else np.nan


def fmt_tbl(df, nd=4):
    return df.round(nd).to_string()
