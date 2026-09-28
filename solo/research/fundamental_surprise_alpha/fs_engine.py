# -*- coding: utf-8 -*-
"""fs_engine: 横截面 Alpha 检验引擎（§12 / §14 / §17-§25 / §33）

设计
  cohort = 事件公告日 k（同一 ann_date 的事件共享同一入场日，天然构成一个横截面）
  特征在 cohort 内做 rank(pct) 与 z 标准化
  前向收益 = 相对沪深300 的超额收益 ex_*（同时保留原始收益 ret_* 供对照）

严格 as-of（§4/§5/§14）
  E1 视角（主）：入场 = D0+1 开盘；可用价格反应 = Return_T0 / Gap / D0日内 / D0量比
  E2 视角：入场 = D0+1 收盘；额外可用 Return_T1 / IntradayReturn_T1
  E3 视角：入场 = D0+3 收盘；额外可用 Return_T3 / PR3
  基本面特征一律在 D0 收盘后即已知，无未来信息。
"""
import os
import sys
import glob
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fs_common import DATA, OUTD, CD, HORIZONS, Log

MIN_N = 10                      # cohort 最小样本
REQ = {'': 10, 'mom': 20, 'val': 12, 'siz': 9, 'ind': 10, 'all': 30}
DEC = 10                        # 分位组数


def tgt(h, anchor='E1'):
    """前向超额收益列名（相对沪深300）"""
    return 'ex_%s_T%d' % (anchor, h)


TGT = {h: tgt(h, 'E1') for h in HORIZONS}


# ---------------------------------------------------------------- 基础工具
def g_rank(df, cols, key='k'):
    """cohort 内 rank(pct)，返回 0~1"""
    out = {}
    for c in cols:
        r = df.groupby(key)[c].rank(method='average')
        n = df.groupby(key)[c].transform('size')
        out[c] = np.where(n > 1, (r - 1) / (n - 1), np.nan)
    return pd.DataFrame(out, index=df.index)


def g_z(df, cols, key='k'):
    out = {}
    for c in cols:
        m = df.groupby(key)[c].transform('mean')
        s = df.groupby(key)[c].transform('std')
        v = (df[c] - m) / s.where(s > 1e-12)
        out[c] = v
    return pd.DataFrame(out, index=df.index)


def _seg(codes):
    if len(codes) == 0:
        return np.array([], dtype=int), np.array([], dtype=int)
    starts = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1]])
    ends = np.r_[starts[1:], len(codes)]
    return starts, ends


def seg_corr(codes, x, y):
    """按 cohort 分段 Spearman IC（x/y 需先做过 rank，且无 NaN）"""
    if len(codes) < 2:
        return np.array([]), np.array([]), np.array([])
    starts, ends = _seg(codes)
    cnt = (ends - starts).astype(float)
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    sx = np.add.reduceat(x, starts)
    sy = np.add.reduceat(y, starts)
    sxx = np.add.reduceat(x * x, starts)
    syy = np.add.reduceat(y * y, starts)
    sxy = np.add.reduceat(x * y, starts)
    mx = sx / cnt
    my = sy / cnt
    cov = sxy / cnt - mx * my
    vx = sxx / cnt - mx * mx
    vy = syy / cnt - my * my
    den = np.sqrt(np.maximum(vx * vy, 0.0))
    r = np.full(len(starts), np.nan)
    ok = den > 1e-12
    r[ok] = cov[ok] / den[ok]
    return starts, cnt, r


def seg_mean(codes, grp, val, ngrp):
    """按 (cohort, 分组) 求均值，返回矩阵 (ncohort, ngrp) 与计数"""
    if len(codes) == 0:
        return np.zeros((0, ngrp)), np.zeros((0, ngrp))
    nc = int(codes.max()) + 1
    k = codes.astype(np.int64) * ngrp + grp.astype(np.int64)
    tot = np.bincount(k, weights=val, minlength=nc * ngrp)
    cnt = np.bincount(k, minlength=nc * ngrp)
    with np.errstate(invalid='ignore', divide='ignore'):
        m = np.where(cnt > 0, tot / np.maximum(cnt, 1), np.nan)
    return m.reshape(nc, ngrp), cnt.reshape(nc, ngrp)


def rank_pct_1d(v):
    r = pd.Series(v).rank(method='average').values
    n = np.isfinite(v).sum()
    if n <= 1:
        return r * np.nan
    return (r - 1) / (n - 1)


# ---------------------------------------------------------------- 特征定义
FUND_REV = ['rev_yoy', 'rev_qoq', 'rev_acc', 'rev_cagr3']
FUND_NP = ['np_yoy', 'np_qoq', 'np_acc', 'dp_yoy', 'dp_qoq', 'dp_acc']
FUND_QUAL = ['ocf_yoy', 'ocf_acc', 'ocf_to_np', 'ocf_to_rev', 'ocf_margin',
             'ocf_margin_chg', 'gpm', 'gpm_chg', 'npm', 'npm_chg',
             'roe', 'roe_chg', 'roic', 'roic_chg']
FUND_BS = ['total_assets_g', 'inventories_g', 'accounts_receiv_g', 'debt_g',
           'goodwill_g', 'fix_assets_g', 'ar_vs_rev', 'ar_vs_rev_chg',
           'inv_vs_rev', 'inv_vs_rev_chg', 'debt_vs_rev', 'debt_vs_rev_chg', 'capex_g']
SURPRISE = ['S1_rev', 'S1_np', 'S1_dp', 'S1_ocf', 'S2_rev', 'S2_np', 'S2_dp', 'S2_ocf',
            'S3_rev', 'S3_np', 'S4_np_vs_rev', 'S4_dp_vs_rev', 'S5_np_vs_ocf']
FUND_ALL = FUND_REV + FUND_NP + FUND_QUAL + FUND_BS + SURPRISE

# §14 价格反应变量（列名与 panel.parquet 对齐；按"何时才可知"分层）
#   E1 = k1 开盘（最早可交易锚点）：仅 Gap 已知
#   E2 = k1 收盘：+ 首日涨跌 / 日内 / 收盘位置 / 量比 / 相对强度
#   E3 = k1+2 收盘：+ 公告后累计 3 日反应
#   E5 = k1+4 收盘：+ 公告后累计 5 日反应
REACT_E1 = ['Gap']
REACT_E2 = REACT_E1 + ['Ret_R1', 'Intra_R1', 'Pos_R1', 'VolR_R1', 'RS_R1']
REACT_E3 = REACT_E2 + ['Cum_R3', 'RS_Cum3']
REACT_E5 = REACT_E3 + ['Cum_R5']
REACT_ALL = REACT_E5
REACT_VIEW = {'E1': REACT_E1, 'E2': REACT_E2, 'E3': REACT_E3, 'E5': REACT_E5}

# §15/§16 组合特征（基本面 Surprise 与价格反应的缺口 / 效率）
REACT_TAG = {'Ret_R1': 'pr1', 'Cum_R3': 'pr3'}
TAG_ANCHOR = {'pr1': 'E2', 'pr3': 'E3', 'S6': 'E1'}


def combo_name(f, react):
    """组合特征列名（唯一来源，供 core / robust / report 共用）"""
    t = REACT_TAG[react]
    return 'RG_%s_%s' % (f, t), 'Eff_%s_%s' % (f, t)


def view_features(df, a):
    """锚点 a 在入场时点真实可知的全部特征列"""
    static = [c for c in (FUND_ALL + MOM_RAW + VALUE_RAW + SIZE_RAW + ['ind_rs'])
              if c in df.columns]
    hist = [c for c in df.columns if c.startswith(('S1h', 'S2h'))]
    combos = [c for c in df.columns if c.startswith(('S6_', 'RG_', 'Eff_'))]
    tag = 'pr1' if a == 'E2' else ('pr3' if a in ('E3', 'E5') else 'S6')
    dep = [c for c in combos if c.startswith('S6_')] if tag == 'S6' else \
        [c for c in combos if c.endswith('_' + tag)]
    vue = static + hist + dep + [c for c in REACT_VIEW[a] if c in df.columns]
    return [c for c in dict.fromkeys(vue)]

VALUE_RAW = ['ep_ttm', 'bp', 'sp', 'dv_ttm']
SIZE_RAW = ['ln_mv', 'turnover_rate']
MOM_RAW = ['mom_5', 'mom_10', 'mom_20', 'mom_60']


def add_derived(df):
    """派生列（一律 as-of，无未来信息）

    注意：ep_ttm / bp 保留负值（亏损公司的 1/PE 为负，天然排在低分位），
    不能置 NaN，否则等于把亏损公司从 Value 控制中剔除，造成样本选择偏差。
    sp（营收市值比）仅对 ps>0 有意义。
    """
    pe = pd.to_numeric(df['pe_ttm'], errors='coerce')
    pb = pd.to_numeric(df['pb'], errors='coerce')
    ps = pd.to_numeric(df['ps'], errors='coerce')
    df['ep_ttm'] = np.where(np.abs(pe) > 1e-9, 1.0 / pe.where(np.abs(pe) > 1e-9), np.nan)
    df['bp'] = np.where(np.abs(pb) > 1e-9, 1.0 / pb.where(np.abs(pb) > 1e-9), np.nan)
    df['sp'] = np.where(ps > 1e-9, 1.0 / ps.where(ps > 1e-9), np.nan)
    df['dv_ttm'] = pd.to_numeric(df['dv_ttm'], errors='coerce')
    mv = pd.to_numeric(df['total_mv'], errors='coerce')
    df['ln_mv'] = np.log(mv.where(mv > 0))
    # cohort 键统一为 'k'（= 公告后首个交易日索引 k1）
    if 'k' not in df.columns:
        df['k'] = df['k1'].astype('int64')
    return df


def add_ind_l2(df):
    fp = os.path.join(CD, 'industry', 'sw_industry_map.csv')
    if not os.path.exists(fp):
        df['ind_l2'] = np.nan
        return df
    d = pd.read_csv(fp, dtype=str)
    m = dict(zip(d['ts_code'], d['l2_name']))
    df['ind_l2'] = df['ts_code'].map(m).fillna(df['ts_code'].map(
        dict(zip(d['ts_code'], d['l1_name']))))
    return df


def add_hist_variants(df, hists=(4, 8, 12)):
    """§32 参数网格：S1/S2 在 4/8/12 季度历史窗口下的变体"""
    df = df.sort_values(['ts_code', 'end_date']).reset_index(drop=True)
    for src, tag in (('np_yoy', 'np'), ('rev_yoy', 'rev')):
        s = df[src]
        sh = s.groupby(df['ts_code']).shift(1)
        codes = df['ts_code']
        for h in hists:
            med = sh.groupby(codes).rolling(h, min_periods=max(2, h // 2)).median()
            med.index = med.index.droplevel(0)
            df['S1h%d_%s' % (h, tag)] = s - med.sort_index()
        for h in hists:
            vals = []
            for _, sub in df.groupby('ts_code', sort=False):
                v = sub[src].values
                out = np.full(len(v), np.nan)
                for i in range(len(v)):
                    hist = v[max(0, i - h):i]
                    hist = hist[np.isfinite(hist)]
                    if np.isfinite(v[i]) and len(hist) >= max(3, h // 2):
                        out[i] = float((hist < v[i]).mean())
                vals.append(pd.Series(out, index=sub.index))
            df['S2h%d_%s' % (h, tag)] = pd.concat(vals).sort_index()
    return df


# ---------------------------------------------------------------- 残差中性化
def neutralize(df, feat_cols, ctrl_cols, key='k', min_n=20, ind_col=None):
    """cohort 内对控制变量回归取残差；ind_col 给定时按 FWL 做行业固定效应

    行业 FE 的正确做法（FWL）：在 cohort 内把 **Y 与 X 同时** 按行业去均值，
    再以去均值后的 X 回归去均值后的 Y（不再含截距）。
    仅对 Y 去均值、X 不去均值会残留行业信息，不是真正的行业中性。

    注意：分段（_seg）要求 cohort 编码在行序上连续，因此必须先按 key 排序，
    计算完再按原顺序回填。df 若未按 key 排序（例如已按 ts_code 排序），
    不排序会导致每个 cohort 被切成 1 行一段而全部返回 NaN。
    """
    Rk = g_rank(df, feat_cols, key).values.astype(float)
    Zk = (g_z(df, ctrl_cols, key).values.astype(float) if ctrl_cols
          else np.zeros((len(df), 0)))
    kv = np.asarray(df[key].values)
    order = np.argsort(kv, kind='stable')
    ks = kv[order]
    Rk = Rk[order]
    Zk = Zk[order]
    codes = pd.factorize(ks, sort=True)[0]          # ks 已排序 → codes 单调不减
    ind = df[ind_col].astype(str).values[order] if ind_col else None
    starts, ends = _seg(codes)
    res = np.full_like(Rk, np.nan)

    def _demean_by(M, gi, ng, den):
        for j in range(M.shape[1]):
            col = M[:, j]
            M[:, j] = col - (np.bincount(gi, weights=col, minlength=ng) / den)[gi]

    for s, e in zip(starts, ends):
        n = e - s
        if n < min_n:
            continue
        Xc = Zk[s:e]
        okc = np.isfinite(Xc).all(axis=1) if Xc.shape[1] else np.ones(n, dtype=bool)
        if okc.sum() < min_n:
            continue
        Ys = np.nan_to_num(Rk[s:e][okc], nan=0.0)
        Xs = Xc[okc]
        nk = Xs.shape[1]

        gi = None
        if ind is not None:
            gsub = ind[s:e][okc]
            gi = pd.factorize(gsub)[0]      # 缺失行业（NA）编码为 -1
            if (gi < 0).any():
                # 行业缺失的行合并为独立一组，避免 bincount 拒绝负编码
                gi = np.where(gi < 0, gi.max() + 1 if gi.max() >= 0 else 0, gi)
            if gi.max() < 1 or (gi.max() + 1) >= len(gi):   # 单行业/每行独立 → 退化
                gi = None

        if gi is not None:
            ng = int(gi.max()) + 1
            den = np.maximum(np.bincount(gi, minlength=ng), 1).astype(float)
            _demean_by(Ys, gi, ng, den)
            if nk:
                _demean_by(Xs, gi, ng, den)
        if nk:
            A = np.column_stack([np.ones(len(Xs)), Xs]) if gi is None else Xs
            try:
                B, *_ = np.linalg.lstsq(A, Ys, rcond=None)
            except Exception:
                continue
            r = Ys - A @ B
        else:
            r = Ys - (Ys.mean(axis=0) if gi is None else 0.0)

        keep = np.flatnonzero(okc)
        blk = res[s:e]
        blk[keep] = r
        res[s:e] = blk

    out = np.full_like(res, np.nan)
    out[order] = res
    return pd.DataFrame(out, index=df.index, columns=feat_cols)


# ---------------------------------------------------------------- §5 前视掩码
# 滞后/滚动期报告的 ann_date **严格晚于** 当期 ann_date → 该期派生值当期未知，置 NaN
# （实测：shift(1) 严格更晚 118/256939=0.05%；TTM 滚动 4 期 108/261865=0.04%；
#   另有 8.2% 为年报与一季报同日披露，属同时公开，不构成前视，不掩码）
ASOF_COLS = [
    'rev_qoq', 'np_qoq', 'dp_qoq',
    'rev_acc', 'np_acc', 'ocf_acc', 'dp_acc',
    'npm_acc', 'gpm_acc', 'roe_acc', 'roic_acc', 'ocf_margin_acc',
    'S1_rev', 'S1_np', 'S1_dp', 'S1_ocf',
    'S2_rev', 'S2_np', 'S2_dp', 'S2_ocf',
    'ocf_to_np', 'ocf_to_rev', 'ocf_margin', 'npm', 'rev_cagr3',
    'ar_vs_rev', 'inv_vs_rev', 'debt_vs_rev',
    'ar_vs_rev_chg', 'inv_vs_rev_chg', 'debt_vs_rev_chg',
    'S1h4_np', 'S1h4_rev', 'S1h8_np', 'S1h8_rev', 'S1h12_np', 'S1h12_rev',
    'S2h4_np', 'S2h4_rev', 'S2h8_np', 'S2h8_rev', 'S2h12_np', 'S2h12_rev',
]


def apply_asof_mask(df):
    """§5：把"当期不可知"的滞后派生值置 NaN，返回 (df, stats)"""
    d = df[['ts_code', 'end_date', 'ann_date']].copy()
    d['_i'] = np.arange(len(d))
    d = d.sort_values(['ts_code', 'end_date'], kind='stable')
    a = d['ann_date'].astype(str).values
    codes = pd.factorize(d['ts_code'].values, sort=False)[0]
    starts, ends = _seg(codes)
    leak1 = np.zeros(len(d), dtype=bool)
    leakT = np.zeros(len(d), dtype=bool)
    for s, e in zip(starts, ends):
        for i in range(s + 1, e):
            if a[i - 1] > a[i]:
                leak1[i] = True
        for i in range(s + 3, e):
            if (a[i - 3:i] > a[i]).any():
                leakT[i] = True
    bad = leak1 | leakT
    cols = [c for c in ASOF_COLS if c in df.columns]
    if bad.any() and cols:
        df.loc[np.asarray(df.index)[d['_i'].values[bad]], cols] = np.nan
    return df, dict(asof_lag1_leak_rows=int(leak1.sum()),
                    asof_ttm_leak_rows=int(leakT.sum()),
                    asof_masked_rows=int(bad.sum()),
                    asof_masked_cols=len(cols))


# ---------------------------------------------------------------- IC / 价差 / 判别
def cohort_ic(df, cols, ycol, min_n=MIN_N):
    """返回 {col: (codes, cnt, ic)}，codes 为 cohort 序号（sorted，与 uniq 对应）"""
    out = {}
    base = df[['k', ycol]].copy()
    for c in cols:
        t = pd.DataFrame({'k': df['k'].values, 'x': np.asarray(df[c], float),
                          'y': np.asarray(base[ycol], float)})
        t = t[np.isfinite(t['x']) & np.isfinite(t['y'])]
        if len(t) == 0:
            out[c] = (np.array([]), np.array([]), np.array([]))
            continue
        t = t.sort_values('k', kind='stable')
        codes, uniq = pd.factorize(t['k'].values, sort=True)
        xr = rank_pct_1d_group(t['x'].values, codes)
        yr = rank_pct_1d_group(t['y'].values, codes)
        _, cnt, ic = seg_corr(codes, xr, yr)
        out[c] = (np.asarray(uniq), cnt, ic)
    return out


def rank_pct_1d_group(v, codes):
    r = pd.Series(v).groupby(codes).rank(method='average').values
    n = pd.Series(v).groupby(codes).transform('size').values
    with np.errstate(invalid='ignore', divide='ignore'):
        return np.where(n > 1, (r - 1) / (n - 1), np.nan)


def ic_stats(codes_uniq, cnt, ic, period_map, min_n=MIN_N):
    """按期聚合 IC"""
    ok = np.isfinite(ic) & (cnt >= min_n)
    res = {}
    for pname in ('ALL', 'TRAIN', 'VALID', 'OOS'):
        if pname == 'ALL':
            m = ok
        else:
            pm = np.array([period_map.get(k, 'PRE') for k in codes_uniq])
            m = ok & (pm == pname)
        v = ic[m]
        if len(v) == 0:
            res[pname] = dict(n_coh=0, ic_mean=np.nan, ic_med=np.nan, ic_std=np.nan,
                              icir=np.nan, pos=np.nan)
            continue
        sd = float(np.std(v, ddof=1)) if len(v) > 1 else np.nan
        res[pname] = dict(n_coh=int(len(v)), ic_mean=float(np.mean(v)),
                          ic_med=float(np.median(v)), ic_std=sd,
                          icir=float(np.mean(v) / sd) if sd and sd > 1e-12 else np.nan,
                          pos=float((v > 0).mean()))
    return res


def spread_stats(df, col, ycol, cost_bps=(0, 10, 20, 30, 50), min_n=MIN_N, min_bin=3):
    """§22 Top-Bottom（Top10/Bottom10、Top20/Bottom20），Gross 与 Net"""
    t = pd.DataFrame({'k': df['k'].values, 'x': np.asarray(df[col], float),
                      'y': np.asarray(df[ycol], float)})
    t = t[np.isfinite(t['x']) & np.isfinite(t['y'])]
    if len(t) == 0:
        return None
    t = t.sort_values('k', kind='stable')
    codes, uniq = pd.factorize(t['k'].values, sort=True)
    xr = rank_pct_1d_group(t['x'].values, codes)
    b = np.where(np.isfinite(xr), np.clip(xr * DEC, 0, DEC - 1), DEC)
    M, C = seg_mean(codes, b, t['y'].values, DEC + 1)
    M = M[:, :DEC]
    C = C[:, :DEC]
    valid = np.isfinite(M) & (C >= min_bin)
    grp = {}
    for name, idxs in (('top10', [9]), ('top20', [8, 9]),
                       ('mid', [4, 5]), ('bot20', [0, 1]), ('bot10', [0])):
        v = np.where(np.all(valid[:, idxs], axis=1),
                     M[:, idxs].mean(axis=1), np.nan)
        grp[name] = v
    t10 = grp['top10'] - grp['bot10']
    t20 = grp['top20'] - grp['bot20']
    out = {'n_coh': int(np.isfinite(t10).sum())}
    for nm, arr in (('top10', grp['top10']), ('bot10', grp['bot10']),
                    ('top20', grp['top20']), ('bot20', grp['bot20']),
                    ('mid', grp['mid'])):
        out['grp_' + nm] = float(np.nanmean(arr)) if np.isfinite(arr).any() else np.nan
    for nm, arr in (('spr10', t10), ('spr20', t20)):
        v = arr[np.isfinite(arr)]
        if len(v) == 0:
            out[nm] = np.nan
            out[nm + '_t'] = np.nan
            continue
        out[nm] = float(np.mean(v))
        out[nm + '_t'] = float(np.mean(v) / (np.std(v, ddof=1) / np.sqrt(len(v)))) \
            if len(v) > 1 and np.std(v, ddof=1) > 1e-12 else np.nan
    for cb in cost_bps:
        out['net%d' % cb] = (out['spr10'] - cb / 10000.0
                             if np.isfinite(out['spr10']) else np.nan)
    return out


def disc_stats(df, col, ycol, q=0.10, min_n=MIN_N):
    """§23 Winner/Loser 判别：AUC / EffectSize / StdGap / RankSeparation / Precision@10%"""
    t = pd.DataFrame({'k': df['k'].values, 'x': np.asarray(df[col], float),
                      'y': np.asarray(df[ycol], float)})
    t = t[np.isfinite(t['x']) & np.isfinite(t['y'])]
    if len(t) == 0:
        return None
    t = t.sort_values('k', kind='stable')
    codes, uniq = pd.factorize(t['k'].values, sort=True)
    xr = rank_pct_1d_group(t['x'].values, codes)
    yr = rank_pct_1d_group(t['y'].values, codes)
    xz = np.where(np.isfinite(xr), xr, np.nan)
    # 用 z(rank) 作为 effect size 尺度
    m = pd.Series(xz).groupby(codes).transform('mean').values
    s = pd.Series(xz).groupby(codes).transform('std').values
    xzz = (xz - m) / s
    win = yr >= (1 - q)
    los = yr <= q
    nc = int(codes.max()) + 1
    nw = np.bincount(codes[win], minlength=nc)
    nl = np.bincount(codes[los], minlength=nc)
    # AUC：在 winner+loser 子集内对 x 排名
    sel = win | los
    cs = codes[sel]
    xsel = t['x'].values[sel]
    rsel = pd.Series(xsel).groupby(cs).rank(method='average').values
    sw = np.bincount(cs[win[sel]], weights=rsel[win[sel]], minlength=nc)
    with np.errstate(invalid='ignore', divide='ignore'):
        auc_c = (sw - nw * (nw + 1) / 2.0) / (nw * nl)
    ok = (nw >= 3) & (nl >= 3) & np.isfinite(auc_c)
    auc = float(np.mean(auc_c[ok])) if ok.any() else np.nan

    mw = pd.Series(t['x'].values[win]).groupby(codes[win]).mean()
    ml = pd.Series(t['x'].values[los]).groupby(codes[los]).mean()
    sw_ = pd.Series(t['x'].values[win]).groupby(codes[win]).std(ddof=1)
    sl_ = pd.Series(t['x'].values[los]).groupby(codes[los]).std(ddof=1)
    allsd = pd.Series(t['x'].values).groupby(codes).std(ddof=1)
    common = mw.index.intersection(ml.index).intersection(allsd.index)
    es = ((mw[common] - ml[common]) / allsd[common]).dropna()
    sg = ((sw_.reindex(common) - sl_.reindex(common)) / allsd[common]).dropna()
    rsw = pd.Series(xr[win]).groupby(codes[win]).mean()
    rsl = pd.Series(xr[los]).groupby(codes[los]).mean()
    rc = rsw.index.intersection(rsl.index)
    rsep = (rsw[rc] - rsl[rc]).dropna()
    # Precision@10%：x 前 10% 中 y 也前 10% 的比例
    topx = xr >= (1 - q)
    prec = pd.Series(win[topx]).groupby(codes[topx]).mean().dropna()
    return dict(auc=auc, eff=float(es.mean()) if len(es) else np.nan,
                stdgap=float(sg.mean()) if len(sg) else np.nan,
                ranksep=float(rsep.mean()) if len(rsep) else np.nan,
                prec=float(prec.mean()) if len(prec) else np.nan,
                n_pair=int(nw[ok].sum()))


def base_cohort_stats(df, col, ycol, min_n=MIN_N):
    """返回逐 cohort 的 ic 序列 + spread 序列（供 walkforward / regime 复用）"""
    t = pd.DataFrame({'k': df['k'].values, 'x': np.asarray(df[col], float),
                      'y': np.asarray(df[ycol], float)})
    t = t[np.isfinite(t['x']) & np.isfinite(t['y'])]
    if len(t) == 0:
        return None
    t = t.sort_values('k', kind='stable')
    codes, uniq = pd.factorize(t['k'].values, sort=True)
    xr = rank_pct_1d_group(t['x'].values, codes)
    yr = rank_pct_1d_group(t['y'].values, codes)
    _, cnt, ic = seg_corr(codes, xr, yr)
    b = np.clip((xr * DEC).astype(float), 0, DEC - 1)
    M, C = seg_mean(codes, np.where(np.isfinite(b), b, DEC), t['y'].values, DEC + 1)
    M = M[:, :DEC]
    valid = np.isfinite(M) & (C[:, :DEC] >= 3)
    spr = np.where(valid[:, 0] & valid[:, 9], M[:, 9] - M[:, 0], np.nan)
    return pd.DataFrame({'k': np.asarray(uniq), 'ic': ic, 'n': cnt, 'spr10': spr})
