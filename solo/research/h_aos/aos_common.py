# -*- coding: utf-8 -*-
"""H-AOS-01 common layer -- Dynamic Alpha OS (Regime x Alpha Health x Selective Trading).

Role of this module
    Single shared layer for every H-AOS-01 step.  It only READS the verified
    shared data layer (hve_common / hrbp_common / tmb_common) and the existing
    Tushare cache; it never modifies another study and never re-builds a data
    system (task text section 3, SPEC section 2).

Frozen contract
    Every threshold, layering rule and condition order below is transcribed
    from H_AOS_01_SPEC.md, which was frozen before any result was observed.
    Editing a number here silently changes the experiment; see SPEC section 21.

Layering (SPEC section 2.3)
    CORE C4 = {A HVT, B Tail, C Breakout, D DIP}  -> full P1..P6
    EXT  X2 = {E ETF,  F Theme}                   -> descriptive only,
                                                     DATA_INSUFFICIENT_FOR_OOS

Look-ahead discipline (SPEC section 20)
    * Regime labels at t use only <= t data.
    * Regime quantiles are computed on the IS window ONLY (2022-01-01 .. 2023-12-31).
    * Alpha Health uses only trades whose exit is already realised (<= t).
    * Forward returns are outcomes; they are never read by a selection rule.
"""
import os
import sys
import glob
import json
import time
import hashlib
import warnings

import numpy as np
import pandas as pd

np.seterr(divide='ignore', invalid='ignore')
warnings.filterwarnings('ignore', message='All-NaN slice encountered')
warnings.filterwarnings('ignore', message='Mean of empty slice')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
HVE_DIR = os.path.join(ROOT, 'research', 'hve')
HRBP_DIR = os.path.join(ROOT, 'research', 'h_rbp')
TMB_DIR = os.path.join(ROOT, 'research', 'h_tail_midbar')
for _p in (HVE_DIR, HRBP_DIR, TMB_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hve_common as H          # noqa: E402  read-only data layer
import hrbp_common as R         # noqa: E402  read-only stats / regime primitives
import tmb_common as T          # noqa: E402  read-only tail-midbar primitives

OUT = HERE
DATA = os.path.join(OUT, 'data')
os.makedirs(DATA, exist_ok=True)
SPEC = os.path.join(HERE, 'H_AOS_01_SPEC.md')

# =====================================================================
# PREREG_AOS -- frozen before any H-AOS-01 result was observed
# =====================================================================
PREREG_AOS = {
    'hypothesis_id': 'H-AOS-01',
    'version': '1.0',
    'created': '2026-10-02',
    'title': 'Dynamic Alpha OS: Regime x Alpha Health x Selective Trading',

    # ---- layering (SPEC section 2.3) ---------------------------------
    'core': ('A', 'B', 'C', 'D'),
    'ext': ('E', 'F'),
    'core_window': ('20220101', '20260924'),

    # ---- universe (SPEC section 2.4) ---------------------------------
    'exclude_bse': True,
    'universe_main': 'B',
    'universe_c_amount_min': 5.0e4,      # thousand CNY -> 50m CNY

    # ---- cost (SPEC section 2.5) -------------------------------------
    'cost_bp': (0, 15, 30, 50),
    'primary_cost_bp': 30,

    # ---- regime (SPEC section 4) -------------------------------------
    'regime_index': '000300.SH',
    'regime_states': ('R1', 'R2', 'R3', 'R4', 'R5'),
    'regime_ma_fast': 20,
    'regime_ma_slow': 60,
    'regime_is': ('20220101', '20231231'),
    'regime_quantiles': {'q_a': ('r20', 0.60), 'q_b': ('r20', 0.25),
                         'q_d': ('r20', 0.05), 'q_v': ('vol20', 0.80),
                         'q_w': ('ln_dn', 0.95), 'q_f': ('breadth', 0.50)},
    'regime_perturb': (0.8, 1.0, 1.2),
    'regime_ma_variants': ((20, 60), (30, 60), (20, 30)),
    'regime_min_share': 0.02,

    # ---- phases (SPEC section 5) -------------------------------------
    'phase_IS': ('2022-01-01', '2023-12-31'),
    'phase_VALID': ('2024-01-01', '2024-12-31'),
    'phase_OOS': ('2025-01-01', '2026-09-24'),

    # ---- alpha definitions (SPEC section 3, verbatim) -----------------
    'A': {'name': 'HVT', 'study_start': '20190101', 'h': 10,
          'source': 'hve_hvt.L6_REEXPANSION'},
    'B': {'name': 'Tail', 'study_start': '20220101', 'h': 1,
          'source': 'tmb_common PRIMARY ARM = B+MA+VOL'},
    'C': {'name': 'Breakout', 'study_start': '20210104', 'h': 3,
          'source': '_brk_build B6_SimpleBreakoutFlag'},
    'D': {'name': 'DIP', 'study_start': '20190101', 'h': 10,
          'source': 'hrbp_common primary_cell'},
    'E': {'name': 'ETF', 'study_start': '20240826', 'h': None,
          'source': 'etf_mainline_strategy_tushare'},
    'F': {'name': 'Theme', 'study_start': '20260708', 'h': None,
          'source': 'theme_heat_v22'},

    # ---- statistics ---------------------------------------------------
    'min_cell_n': 30,
    'boot_B': 1000,
    'null_B': 1000,
    'seed': 20261101,

    'trading_authorization': 'NO',
}

ALPHA_H = {k: PREREG_AOS[k]['h'] for k in ('A', 'B', 'C', 'D')}
ALPHA_NAME = {k: PREREG_AOS[k]['name'] for k in 'ABCDEF'}
CORE = PREREG_AOS['core']
EXT = PREREG_AOS['ext']
COSTS = PREREG_AOS['cost_bp']
COST_PRIMARY = PREREG_AOS['primary_cost_bp']
RSTATES = PREREG_AOS['regime_states']


# =====================================================================
# logging / io
# =====================================================================
class Log(object):
    def __init__(self, name='aos'):
        self.path = os.path.join(OUT, '%s.log' % name)
        self.t0 = time.time()
        with open(self.path, 'w', encoding='utf-8'):
            pass

    def __call__(self, msg):
        line = '[%7.1fs] %s' % (time.time() - self.t0, msg)
        print(line)
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(line + '\n')

    def sep(self, ch='-', n=72):
        self(ch * n)


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for blk in iter(lambda: f.read(1 << 20), b''):
            h.update(blk)
    return h.hexdigest().upper()


def save_csv(df, name):
    p = name if os.path.isabs(name) else os.path.join(DATA, name)
    df.to_csv(p, index=False, encoding='utf-8-sig')
    return p


def save_json(obj, name):
    p = name if os.path.isabs(name) else os.path.join(DATA, name)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=str)
    return p


def fmt(x, nd=4):
    if x is None:
        return 'NA'
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    if not np.isfinite(v):
        return 'nan'
    return ('%.' + str(nd) + 'f') % v


def fmtp(x, nd=2):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 'NA'
    if not np.isfinite(v):
        return 'nan'
    return ('%.' + str(nd) + 'f%%') % (100.0 * v)


# =====================================================================
# panel
# =====================================================================
def load_panel(lg=None):
    """Shared panel + PIT indicators + the three universe layers.

    Returns dict: g, ind, px, el (Universe B), uni (dict A/B/C).
    """
    g = H.build_grid(lg=lg, use_cache=True)
    ind, px, ma = H.build_indicators(g, lg=lg, use_cache=True)
    el = H.eligibility(g) & (g['board'] != 'BSE')[:, None]
    uni = universe_layers(g, el)
    if lg:
        S, N = g['close'].shape
        lg('panel S=%d N=%d  %s..%s' % (S, N, g['dates'][0], g['dates'][-1]))
        for k in ('A', 'B', 'C'):
            lg('  universe %s cells %.4f' % (k, float(uni[k].mean())))
    return {'g': g, 'ind': ind, 'px': px, 'ma': ma, 'el': el, 'uni': uni}


def universe_layers(g, el):
    """SPEC section 2.4 -- three layers; the main table uses B."""
    base = g['traded'] & ~g['st'] & ~g['delist']
    return {
        'A': base,
        'B': el,
        'C': el & (g['amount'] >= PREREG_AOS['universe_c_amount_min']),
    }


def _roll_max_prev_rows(M, k):
    """(E,K) -> (E,K): at column w, the max of columns w-k .. w-1."""
    d = pd.DataFrame(np.asarray(M, dtype='float64').T)
    return d.shift(1).rolling(int(k), min_periods=int(k)).max(
    ).to_numpy().T.astype('float32')


def _vr_excl(M, w):
    """Volume[t] / mean(Volume[t-w .. t-1]); the denominator excludes t."""
    m = T._roll(M, w, mp=w)
    prev = np.full(m.shape, np.nan, dtype='float32')
    prev[:, 1:] = m[:, :-1]
    with np.errstate(invalid='ignore'):
        return np.where(prev > 0, M / prev, np.nan).astype('float32')


def _day_mask(dates, start):
    m = np.zeros(len(dates), dtype=bool)
    m[int(np.searchsorted(dates, str(start), side='left')):] = True
    return m


# =====================================================================
# regime (SPEC section 4) -- 5 states R1..R5, all PIT
# =====================================================================
def hs300_series(g):
    """CSI300 close / amount aligned to the trading calendar (ffill)."""
    p = os.path.join(H.FS_DATA, 'index_panel.parquet')
    idx = pd.read_parquet(p)
    idx['trade_date'] = idx['trade_date'].astype(str)
    idx = idx[idx['ts_code'].astype(str) == PREREG_AOS['regime_index']]
    idx = idx.sort_values('trade_date')
    kmap = g['d2i']
    k = pd.to_numeric(pd.Series(idx['trade_date'].map(kmap).to_numpy()),
                      errors='coerce').to_numpy()
    out = {}
    for f in ('close', 'amount'):
        v = pd.to_numeric(idx[f], errors='coerce').to_numpy()
        z = np.full(len(g['dates']), np.nan)
        m = np.isfinite(k) & np.isfinite(v)
        z[k[m].astype(int)] = v[m]
        out[f] = pd.Series(z).ffill().to_numpy()
    return out


def regime_features(g, el, lg=None):
    """SPEC section 4.1 -- every feature uses information <= t."""
    hs = hs300_series(g)
    cl, amt = hs['close'], hs['amount']
    s_cl = pd.Series(cl)
    ma20 = s_cl.rolling(PREREG_AOS['regime_ma_fast'], min_periods=15).mean().to_numpy()
    ma30 = s_cl.rolling(30, min_periods=22).mean().to_numpy()
    ma60 = s_cl.rolling(PREREG_AOS['regime_ma_slow'], min_periods=40).mean().to_numpy()
    r20 = np.full(len(cl), np.nan)
    r20[20:] = cl[20:] / cl[:-20] - 1.0
    r60 = np.full(len(cl), np.nan)
    r60[60:] = cl[60:] / cl[:-60] - 1.0
    ret = np.full(len(cl), np.nan)
    ret[1:] = cl[1:] / cl[:-1] - 1.0
    vol20 = pd.Series(ret).rolling(20, min_periods=15).std().to_numpy() * np.sqrt(252.0)

    valid = el.sum(axis=0).astype('float64')
    up = ((g['pct_chg'] > 0) & el).sum(axis=0).astype('float64')
    dn = ((g['limdn']) & el).sum(axis=0).astype('float64')
    with np.errstate(invalid='ignore'):
        breadth = np.where(valid > 0, up / valid, np.nan)
        ln_dn = np.where(valid > 0, dn / valid, np.nan)
    a_ma = pd.Series(amt).rolling(20, min_periods=15).mean().to_numpy()
    with np.errstate(invalid='ignore'):
        liq = np.where(a_ma > 0, amt / a_ma, np.nan)

    feat = {'close': cl, 'amount': amt, 'ma20': ma20, 'ma30': ma30,
            'ma60': ma60, 'r20': r20, 'r60': r60, 'vol20': vol20,
            'breadth': breadth, 'ln_dn': ln_dn, 'liq': liq}
    if lg:
        lg('regime features ready  r20 finite %.3f  vol20 finite %.3f'
           % (float(np.isfinite(r20).mean()), float(np.isfinite(vol20).mean())))
    return feat


def regime_thresholds(feat, dates, is_a=None, is_b=None):
    """SPEC section 4.2 -- quantiles computed on the IS window ONLY."""
    is_a = is_a or PREREG_AOS['regime_is'][0]
    is_b = is_b or PREREG_AOS['regime_is'][1]
    m = (dates >= is_a) & (dates <= is_b)
    out = {}
    for k, (col, q) in PREREG_AOS['regime_quantiles'].items():
        v = np.asarray(feat[col])[m]
        v = v[np.isfinite(v)]
        out[k] = float(np.quantile(v, q)) if v.size else np.nan
    out['n_is_days'] = int(m.sum())
    out['_window'] = [is_a, is_b]
    return out


def _scale_thr(thr, k):
    """Scale every quantile threshold by k (the min-share guard is not a
    threshold and is carried through untouched)."""
    return {key: (float(val) * k if key in PREREG_AOS['regime_quantiles']
                  else val) for key, val in thr.items()}


def classify(feat, thr, ma_pair=None):
    """SPEC section 4.3 -- first hit wins, order R5 -> R1 -> R4 -> R2 -> R3.

    `ma_pair` selects which moving averages the rule reads (the 20/30/60
    perturbation of SPEC section 11).  Defaults to (ma20, ma60).
    """
    fk, sk = ma_pair or ('ma20', 'ma60')
    fk = fk if str(fk).startswith('ma') else 'ma%d' % int(fk)
    sk = sk if str(sk).startswith('ma') else 'ma%d' % int(sk)
    cl = feat['close']
    a, b = feat[fk], feat[sk]
    r20 = feat['r20']
    v, w, f = feat['vol20'], feat['ln_dn'], feat['breadth']
    ok = (np.isfinite(cl) & np.isfinite(a) & np.isfinite(b) & np.isfinite(r20)
          & np.isfinite(v) & np.isfinite(w) & np.isfinite(f))
    lab = np.full(len(cl), 'NA', dtype=object)
    R5 = ((v >= thr['q_v']) & (r20 <= 0)) | (w >= thr['q_w']) | (r20 <= thr['q_d'])
    R1 = (cl > a) & (a > b) & (r20 >= thr['q_a']) & (f >= thr['q_f'])
    R4 = (cl < a) & (cl < b) & (r20 < 0) & (f < thr['q_f'])
    R2 = (cl > a) & (cl > b) & (r20 >= 0)
    lab[ok] = 'R3'
    lab[ok & R2] = 'R2'
    lab[ok & R4] = 'R4'
    lab[ok & R1] = 'R1'
    lab[ok & R5] = 'R5'
    return lab.astype(str)


def regime_series(g, el, thr=None, lg=None):
    """-> (labels (N,) str, feat, thresholds)."""
    feat = regime_features(g, el, lg=lg)
    thr = thr or regime_thresholds(feat, g['dates'])
    lab = classify(feat, thr)
    if lg:
        cnt = {s: int((lab == s).sum()) for s in RSTATES}
        lg('regime thresholds %s' % {k: round(v, 4) for k, v in thr.items()
                                     if k in PREREG_AOS['regime_quantiles']})
        lg('regime mix (full panel) %s' % cnt)
    return lab, feat, thr


# ---------------------------------------------------------------- stability
def run_stats(labels, valid=None):
    """Transition count / average duration / whipsaw rate of a label series."""
    x = np.asarray(labels)
    if valid is not None:
        x = x[np.asarray(valid)]
    if x.size == 0:
        return {'n_days': 0, 'n_runs': 0, 'transitions': 0,
                'avg_duration': np.nan, 'whipsaw_rate': np.nan}
    chg = np.flatnonzero(x[1:] != x[:-1]) + 1
    bounds = np.r_[0, chg, x.size]
    dur = np.diff(bounds)
    return {'n_days': int(x.size), 'n_runs': int(dur.size),
            'transitions': int(dur.size - 1),
            'avg_duration': float(dur.mean()),
            'whipsaw_rate': float((dur <= 2).mean())}


def regime_stability(feat, dates, thr0, lg=None):
    """SPEC section 11 -- threshold x {0.8,1.0,1.2} x MA {20/60,30/60,20/30}."""
    base = classify(feat, thr0)
    okb = base != 'NA'
    rows = []
    for k in PREREG_AOS['regime_perturb']:
        for pair in PREREG_AOS['regime_ma_variants']:
            lab = classify(feat, _scale_thr(thr0, k), ma_pair=pair)
            m = okb & (lab != 'NA')
            agree = float((lab[m] == base[m]).mean()) if m.any() else np.nan
            rs = run_stats(lab, valid=(lab != 'NA'))
            rows.append({'thr_scale': k, 'ma_fast': pair[0], 'ma_slow': pair[1],
                         'agreement': agree,
                         'transitions': rs['transitions'],
                         'avg_duration': rs['avg_duration'],
                         'whipsaw_rate': rs['whipsaw_rate']})
            if lg:
                lg('  stability thr x%.1f MA(%d,%d) agree %.3f  trans %d  '
                   'dur %.1f  whipsaw %.3f'
                   % (k, pair[0], pair[1], agree, rs['transitions'],
                      rs['avg_duration'], rs['whipsaw_rate']))
    df = pd.DataFrame(rows)
    base_rs = run_stats(base, valid=okb)
    weak = bool((df[df['agreement'].notna()]['agreement'] < 0.80).any()
                or (base_rs['whipsaw_rate'] > 0.30))
    if lg:
        lg('  baseline: trans %d  avg dur %.1f  whipsaw %.3f  -> %s'
           % (base_rs['transitions'], base_rs['avg_duration'],
              base_rs['whipsaw_rate'], 'WEAK' if weak else 'STABLE'))
    return df, base_rs, weak


# =====================================================================
# alpha event masks (SPEC section 3) -- CORE C4
# =====================================================================
def _pack(g, alpha, si, t0, entry, lg=None):
    """Common trade table for one alpha."""
    si = np.asarray(si, dtype='int64')
    t0 = np.asarray(t0, dtype='int64')
    entry = np.asarray(entry, dtype='int64')
    N = g['close'].shape[1]
    valid = (entry >= 0) & (entry <= N - 1)
    keep = valid
    si, t0, entry = si[keep], t0[keep], entry[keep]
    df = pd.DataFrame({'alpha': alpha, 's_i': si.astype('int32'),
                       't0': t0.astype('int32'), 'entry': entry.astype('int32')})
    df['ts_code'] = g['codes'][df['s_i'].to_numpy()]
    df['entry_date'] = g['dates'][df['entry'].to_numpy()]
    df['year'] = df['entry_date'].str[:4].astype(int)
    df['month'] = df['entry_date'].str[:6].astype(int)
    df['board'] = g['board'][df['s_i'].to_numpy()]
    if lg:
        lg('alpha %s : %d trades  (%s .. %s)'
           % (alpha, len(df), df['entry_date'].min() if len(df) else '-',
              df['entry_date'].max() if len(df) else '-'))
    return df.reset_index(drop=True)


def alpha_A_hvt(g, ind, px, el, lg=None):
    """SPEC section 3 Alpha A -- HVE mother event -> L6 re-expansion confirm.

    Entry = first session in [t0+1, t0+20] satisfying all six L6 conditions,
    booked at that session's CLOSE.  Primary exit = buy+10 close.
    """
    pg = H.PREREG['price_gate_C']
    gate_C = ((g['pct_chg'] >= pg['pct_chg_min']) & (px['clv'] >= pg['clv_min'])
              & (px['body'] >= pg['body_min']) & (g['close'] > px['ma20']))
    m = ((ind['VR20'] >= 2.0) & el & gate_C)
    m2 = H.cluster_events_ranked(m, 5, g['amount'])
    si, di = np.nonzero(m2)
    if lg:
        lg('  A t0 events %d' % si.size)
    cond, _, _ = _hvt_conditions(g, ind, px, si, di)
    keys = ('T_CLOSE_GT_MA20', 'T_MA20_SLOPE_UP', 'S_CLOSE_GT_HVE_CLOSE',
            'S_CLOSE_GT_HVE_HIGH', 'N_NO_STRUCTURAL_DAMAGE', 'R_REEXPANSION')
    allc = cond[keys[0]].copy()
    for k in keys[1:]:
        allc &= cond[k]
    hit = allc.any(axis=1)
    j = np.argmax(allc, axis=1)
    entry = np.where(hit, di + j + 1, -1)
    if lg:
        lg('  A L6 confirmed %d / %d  (%.2f%%)'
           % (int(hit.sum()), si.size, 100.0 * hit.sum() / max(1, si.size)))
    si, di, entry = si[hit], di[hit], entry[hit]
    df = _pack(g, 'A', si, di, entry, lg=lg)
    df['ret'] = H.fwd_ret(g, df['s_i'].to_numpy(), df['entry'].to_numpy(),
                          ALPHA_H['A'], field='close', base='close')
    df['hold'] = ALPHA_H['A']
    return df


def _hvt_conditions(g, ind, px, si, di):
    """Transcribed verbatim from hve_hvt.build_conditions (read-only reuse)."""
    F = H.PREREG['hvt_confirm_max_wait']
    cl, okw = H.win_post(g['close'], si, di, F)
    ma20 = H.win_post(px['ma20'], si, di, F)[0]
    slope = H.win_post(px['ma20_slope5'], si, di, F)[0]
    vma = H.win_post(ind['VOL_MA20'], si, di, F)[0]
    hi20 = H.win_post(px['hi20'], si, di, F)[0]
    hi10 = H.win_post(H._roll_max_prev(g['high'], 10), si, di, F)[0]
    hv_close = g['close'][si, di].astype('float64')[:, None]
    hv_high = g['high'][si, di].astype('float64')[:, None]
    ratio = np.where(okw & (hv_close > 0), cl / hv_close, np.nan)
    dd_run = np.fmin.accumulate(np.where(np.isfinite(ratio), ratio, np.inf),
                                axis=1) - 1.0
    with np.errstate(invalid='ignore'):
        dist_hi20 = np.where(okw & (hi20 > 0), cl / hi20 - 1.0, np.nan)
    bull_hold = (cl > ma20) & (slope > 0) & (cl >= 0.95 * hv_close) & okw
    bull_t0 = ((g['close'][si, di] > px['ma20'][si, di])
               & (px['ma20_slope5'][si, di] > 0))
    prev_bull = np.empty_like(bull_hold)
    prev_bull[:, 0] = bull_t0
    prev_bull[:, 1:] = bull_hold[:, :-1]
    cond = {
        'T_CLOSE_GT_MA20': (cl > ma20) & okw,
        'T_MA20_SLOPE_UP': (slope > 0) & okw,
        'S_CLOSE_GT_HVE_CLOSE': (cl > hv_close) & okw,
        'S_CLOSE_GT_HVE_HIGH': (cl > hv_high) & okw,
        'N_NO_STRUCTURAL_DAMAGE': (dd_run >= -0.10) & okw,
        'R_REEXPANSION': ((cl > ma20) & (vma >= 1.0) & (cl > hi10)
                          & ~prev_bull & okw),
    }
    return cond, dist_hi20, dd_run


def alpha_B_tail(g, px, el, lg=None):
    """SPEC section 3 Alpha B -- first >=3% bullish mid-bar, PRIMARY ARM.

    E0 & First(10) & MApresent & V5 ; buy = T close, exit = T+1 close.
    """
    P = T.PREREG_T
    cl = g['close']
    volm = np.where(g['traded'], g['vol'], np.nan).astype('float32')
    ma5 = T._roll(cl, P['ma_fast'])
    ma20 = T._roll(cl, P['ma_slow'])
    s5 = T._slope(ma5, P['ma_slope'], P['ma_slope'])
    s20 = T._slope(ma20, P['ma_slope'], P['ma_slope'])
    M, MAANY, _ = T.ma_masks(cl, ma5, ma20, s5, s20)
    vr20 = _vr_excl(volm, P['vr_win'])
    hl = g['high'] - g['low']
    with np.errstate(invalid='ignore'):
        body = np.where(g['open'] > 0, (cl - g['open']) / g['open'], np.nan)
        clv = np.where(hl > 0, (cl - g['low']) / hl, np.nan)
    day_ok = _day_mask(g['dates'], P['study_start'])
    E0 = (el & (g['pct_chg'] >= P['thr_primary']) & (cl > g['open'])
          & (body >= P['body_min']) & (clv >= P['clv_min'])
          & np.isfinite(g['pct_chg']))
    first = T.first_mask(E0, P['first_primary'])
    lo, hi = P['vr_gates'][P['vr_primary']]
    V5 = (vr20 >= lo) & (vr20 <= hi)
    sig = E0 & first & MAANY & V5 & day_ok[None, :]
    si, ti = np.nonzero(sig)
    if lg:
        lg('  B signals %d  (E0 %d)' % (si.size, int(E0.sum())))
    df = _pack(g, 'B', si, ti, ti, lg=lg)
    df['ret'] = H.fwd_ret(g, df['s_i'].to_numpy(), df['entry'].to_numpy(),
                          ALPHA_H['B'], field='close', base='close')
    df['hold'] = ALPHA_H['B']
    return df


def alpha_C_breakout(g, px, el, lg=None):
    """SPEC section 3 Alpha C -- first close above the trailing 20-day high.

    Pool conditions verbatim from the original model (seq>=60, amount>=5000万,
    ~ST, ~delist, board!=BSE).  Buy = T+1 OPEN, exit = T+1+3 close.
    """
    N = g['close'].shape[1]
    idx = np.arange(N)[None, :]
    seq = idx - g['first_idx'][:, None]
    r20 = H._roll_max_prev(g['high'], 20).astype('float64')
    with np.errstate(invalid='ignore'):
        dist = np.where(r20 > 0, g['close'].astype('float64') / r20 - 1.0, np.nan)
    pool = ((seq >= 60) & np.isfinite(g['close']) & (g['close'] > 0)
            & np.isfinite(g['vol']) & (g['vol'] > 0)
            & np.isfinite(g['amount']) & ~g['st'] & ~g['delist']
            & (g['board'] != 'BSE')[:, None]
            & (g['amount'] >= 5.0e4))
    day_ok = _day_mask(g['dates'], PREREG_AOS['C']['study_start'])
    m = pool & np.isfinite(dist) & (dist > 0) & day_ok[None, :]
    si, ti = np.nonzero(m)
    if lg:
        lg('  C breakout events %d  (pool cells %.4f)'
           % (si.size, float(pool.mean())))
    df = _pack(g, 'C', si, ti, ti + 1, lg=lg)
    df['ret'] = H.fwd_ret(g, df['s_i'].to_numpy(), df['entry'].to_numpy(),
                          ALPHA_H['C'], field='close', base='open')
    df['hold'] = ALPHA_H['C']
    return df


def alpha_D_dip(g, px, el, lg=None):
    """SPEC section 3 Alpha D -- HVE anchor -> R1 retracement + structure +
    5-day re-strength.  Buy = signal+1 OPEN, exit = buy+10 close."""
    P = R.PREREG_H
    W, BACK = P['window'], P['lookback']
    A0, B1 = BACK + 1, BACK + 1 + W
    vrp = R.vr20_excl_panel(g)
    hl = (g['high'] - g['low']).astype('float64')
    with np.errstate(invalid='ignore'):
        clv = np.where(hl > 0, (g['close'] - g['low']) / hl, np.nan)
    r1 = px['r1']
    above_open = g['close'] > g['open']
    day_ok = _day_mask(g['dates'], P['study_start'])
    core = np.isfinite(vrp) & np.isfinite(clv) & np.isfinite(r1) & above_open
    raw = (core & el & (vrp >= P['anchor_vr20_min'])
           & (r1 >= P['anchor_ret_min']) & (clv >= P['anchor_clv_min'])
           & day_ok[None, :])
    ded = H.cluster_events_ranked(raw, P['anchor_cluster_gap'], g['amount'])
    si, t0 = np.nonzero(ded)
    need = W + 1 + max(P['horizons'])
    edge = (t0 + need) <= (g['close'].shape[1] - 1)
    si, t0 = si[edge], t0[edge]
    if lg:
        lg('  D anchors %d  (raw %d, dropped right-edge %d)'
           % (si.size, int(raw.sum()), int((~edge).sum())))
    if si.size == 0:
        return _pack(g, 'D', si, t0, np.zeros(0, dtype='int64'), lg=lg)
    hi = R.gather_win(g['high'], si, t0, BACK, W)[0]
    lo = R.gather_win(g['low'], si, t0, BACK, W)[0]
    cl = R.gather_win(g['close'], si, t0, BACK, W)[0]
    ma20 = R.gather_win(px['ma20'], si, t0, BACK, W)[0]
    cw, hw, lw, mw = cl[:, A0:B1], hi[:, A0:B1], lo[:, A0:B1], ma20[:, A0:B1]
    l0 = lo[:, BACK][:, None]
    ehigh = np.fmax.accumulate(
        np.concatenate([hi[:, BACK][:, None], hw], axis=1), axis=1)[:, 1:]
    with np.errstate(invalid='ignore'):
        dd = np.where(cw > 0, cw / np.where(ehigh > 0, ehigh, np.nan) - 1.0, np.nan)
    band = (dd <= -0.03) & (dd > -0.05)                       # R1 band
    s3 = (cw >= mw) & (lw >= l0)                              # S3, at RETRACE
    rm5 = _roll_max_prev_rows(hi, 5)[:, A0:B1]
    rs2 = cw > rm5                                            # RS2_5D
    p = R.first_from(band, np.zeros(len(si), dtype='int16'))
    okp = p >= 0
    ar = np.arange(len(si))
    gate = np.where(okp, s3[ar, np.clip(p, 0, W - 1)], False)
    pos = R.first_from(rs2, np.where(okp & gate, p, -1))
    entry = np.where(pos >= 0, t0 + pos + 2, -1)
    if lg:
        lg('  D signals %d / %d anchors  (%.2f%%)'
           % (int((entry >= 0).sum()), si.size,
              100.0 * (entry >= 0).sum() / max(1, si.size)))
    t0 = np.asarray(t0)
    df = _pack(g, 'D', si, t0, entry, lg=lg)
    df['ret'] = H.fwd_ret(g, df['s_i'].to_numpy(), df['entry'].to_numpy(),
                          ALPHA_H['D'], field='close', base='open')
    df['hold'] = ALPHA_H['D']
    return df


# =====================================================================
# EXT layer -- descriptive only (SPEC section 2.3)
# =====================================================================
def alpha_E_etf(g, lg=None):
    """SPEC section 3 Alpha E -- 20-day momentum, TOP_N=1, REBAL_DAYS=30.

    Asset pool = the ETF close series available in the existing cache; the
    static-pool selection bias is registered in SPEC section 19.  Declared
    DATA_INSUFFICIENT_FOR_OOS -- descriptive evidence only.
    """
    d = os.path.join(H.CD, 'etf_backtest_hist')
    files = sorted(glob.glob(os.path.join(d, '*.csv')))
    rows = []
    for f in files:
        code = os.path.basename(f)[:-4]
        if code.startswith('idx_'):
            continue
        try:
            x = pd.read_csv(f, dtype={'trade_date': str})
        except Exception:
            continue
        if not {'trade_date', 'close'} <= set(x.columns):
            continue
        x = x[['trade_date', 'close']].copy()
        x['code'] = code
        rows.append(x)
    if not rows:
        return pd.DataFrame(), {'status': 'DATA_INSUFFICIENT_FOR_OOS',
                                'reason': 'no etf csv'}
    a = pd.concat(rows, ignore_index=True)
    a['di'] = a['trade_date'].map(g['d2i'])
    a = a.dropna(subset=['di'])
    a['di'] = a['di'].astype('int64')
    piv = a.pivot_table(index='di', columns='code', values='close').sort_index()
    piv = piv.dropna(axis=1, thresh=40)
    pxm = piv.to_numpy(dtype='float64')
    idxs = piv.index.to_numpy()
    pxs = pd.DataFrame(pxm)
    mom = pxs / pxs.shift(20) - 1.0
    rank = mom.rank(axis=1, pct=True).to_numpy() * 100.0
    rebal, min_hold, exit_pct = 30, 5, 0.30
    trades = []
    pos, ei = None, None
    D = len(idxs)
    for i in range(D):
        if pos is not None:
            held = i - ei
            r = rank[i, pos] if np.isfinite(rank[i, pos]) else np.nan
            trigger = (held >= min_hold and (not np.isfinite(r)
                                             or r < (1.0 - exit_pct) * 100.0))
            trigger = trigger or (held >= rebal) or (i == D - 1)
            if trigger and np.isfinite(pxm[i, pos]) and np.isfinite(pxm[ei, pos]):
                trades.append((idxs[ei], idxs[i], pxm[i, pos] / pxm[ei, pos] - 1.0))
                pos = None
        if pos is None and i < D - 1:
            r = rank[i]
            if np.isfinite(r).any():
                pos = int(np.nanargmax(np.where(np.isfinite(r), r, -np.inf)))
                ei = i
    if not trades:
        return pd.DataFrame(), {'status': 'DATA_INSUFFICIENT_FOR_OOS',
                                'reason': 'no rebalances'}
    e = np.array([t[0] for t in trades], dtype='int64')
    x_ = np.array([t[1] for t in trades], dtype='int64')
    r_ = np.array([t[2] for t in trades], dtype='float64')
    df = pd.DataFrame({'alpha': 'E', 'entry': e, 't0': e, 'is_gross': True})
    df['s_i'] = -1
    df['exit'] = x_
    df['ret'] = r_
    df['hold'] = (x_ - e).astype('int32')
    df['entry_date'] = g['dates'][e]
    df['year'] = df['entry_date'].str[:4].astype(int)
    df['month'] = df['entry_date'].str[:6].astype(int)
    df['ts_code'] = ''
    df['board'] = ''
    meta = {'status': 'DATA_INSUFFICIENT_FOR_OOS', 'n_trades': int(len(df)),
            'n_etf': int(piv.shape[1]), 'first': str(df['entry_date'].min()),
            'last': str(df['entry_date'].max())}
    if lg:
        lg('  E ETF rebalances %d  (%s .. %s)'
           % (len(df), meta['first'], meta['last']))
    return df, meta


def alpha_F_theme(g, lg=None):
    """SPEC section 3 Alpha F -- declared DATA_INSUFFICIENT_FOR_OOS.

    The theme heat series covers ~60 sessions and the stock->theme map exists
    for only a handful of recent snapshots, so no PIT stock-level signal can
    be rebuilt.  The metadata records the audited coverage instead of a
    fabricated number.
    """
    p = os.path.join(H.REPORT_DAILY, 'theme_heat_series.csv')
    meta = {'status': 'DATA_INSUFFICIENT_FOR_OOS',
            'reason': 'heat series ~60 sessions; theme_stock_map snapshots '
                      '<10 and all recent -> no PIT stock-level rebuild'}
    if os.path.exists(p):
        x = pd.read_csv(p, dtype={'date': str})
        meta.update({'n_rows': int(len(x)),
                     'n_dates': int(x['date'].nunique()) if 'date' in x else 0,
                     'first': str(x['date'].min()) if 'date' in x else '',
                     'last': str(x['date'].max()) if 'date' in x else ''})
    meta['n_snapshots'] = len(glob.glob(os.path.join(
        H.CD, 'theme_stock_map_v2_*.json')))
    if lg:
        lg('  F theme -> %s (%s)' % (meta['status'], meta.get('reason', '')))
    return pd.DataFrame(), meta


def clip_core(df):
    """SPEC section 2.3 -- keep only trades inside the shared CORE window.

    The four CORE alphas have different native study windows; the shared
    window is what makes them comparable, so every P1..P6 table is built on
    this intersection.
    """
    if df is None or len(df) == 0:
        return df
    a, b = PREREG_AOS['core_window']
    d = df['entry_date'].astype(str)
    return df[(d >= a) & (d <= b)].reset_index(drop=True)


def build_alphas(panel, which=None, lg=None):
    """-> (trades DataFrame for CORE, ext_trades list, ext_meta dict)."""
    g, ind, px, el = panel['g'], panel['ind'], panel['px'], panel['el']
    which = which or CORE
    parts, ext, ext_meta = [], [], {}
    if 'A' in which:
        parts.append(alpha_A_hvt(g, ind, px, el, lg=lg))
    if 'B' in which:
        parts.append(alpha_B_tail(g, px, el, lg=lg))
    if 'C' in which:
        parts.append(alpha_C_breakout(g, px, el, lg=lg))
    if 'D' in which:
        parts.append(alpha_D_dip(g, px, el, lg=lg))
    if 'E' in which:
        d, m = alpha_E_etf(g, lg=lg)
        ext_meta['E'] = m
        if len(d):
            ext.append(d)
    if 'F' in which:
        d, m = alpha_F_theme(g, lg=lg)
        ext_meta['F'] = m
        if len(d):
            ext.append(d)
    core = (pd.concat(parts, ignore_index=True) if parts
            else pd.DataFrame(columns=['alpha', 's_i', 't0', 'entry', 'ret']))
    core = clip_core(core)
    if lg and len(core):
        lg('core window %s..%s -> %d trades'
           % (core['entry_date'].min(), core['entry_date'].max(), len(core)))
    return core, ext, ext_meta


def attach_regime(df, labels, dates):
    """PIT regime label at the entry session (the session the trade is opened)."""
    if df is None or len(df) == 0:
        return df
    e = np.clip(df['entry'].to_numpy(), 0, len(labels) - 1)
    df = df.copy()
    df['regime'] = labels[e]
    t = np.clip(df['t0'].to_numpy(), 0, len(labels) - 1)
    df['regime_sig'] = labels[t]
    return df


def phase_of(date_str):
    d = str(date_str)
    for ph in ('IS', 'VALID', 'OOS'):
        a, b = PREREG_AOS['phase_' + ph]
        if a.replace('-', '') <= d <= b.replace('-', ''):
            return ph
    return 'PRE' if d < PREREG_AOS['phase_IS'][0].replace('-', '') else 'OUT'


# =====================================================================
# statistics
# =====================================================================
def _monthly_book(ret, months):
    """Equal-weight monthly book -> (sharpe, mdd, worst_month, n_month)."""
    r = np.asarray(ret, dtype='float64')
    m = np.asarray(months)
    ok = np.isfinite(r)
    if ok.sum() < 2:
        return np.nan, np.nan, np.nan, 0
    s = pd.Series(r[ok]).groupby(m[ok]).mean().sort_index()
    if len(s) < 2:
        return np.nan, np.nan, (float(s.iloc[0]) if len(s) else np.nan), len(s)
    sd = float(s.std(ddof=1))
    sharpe = (float(s.mean()) / sd * np.sqrt(12.0)) if sd > 0 else np.nan
    curve = (1.0 + s).cumprod()
    mdd = float((curve / curve.cummax() - 1.0).min())
    return sharpe, mdd, float(s.min()), int(len(s))


def cell_stats(ret, months=None):
    """One Alpha x Regime cell (SPEC section 7 column set)."""
    r = np.asarray(ret, dtype='float64')
    mth = (np.asarray(months) if months is not None
           else np.zeros(r.size, dtype='int64'))
    ok = np.isfinite(r)
    r, mth = r[ok], mth[ok]
    d = {'n': int(r.size)}
    keys = ('win_rate', 'mean', 'median', 'pf', 't', 'p05', 'p95', 'std',
            'mean_win', 'mean_loss')
    if r.size == 0:
        for k in keys:
            d[k] = np.nan
        for k in ('mean_net15', 'mean_net30', 'mean_net50', 'pf_net30',
                  'win_net30', 'sharpe', 'mdd', 'worst_month', 'n_month'):
            d[k] = np.nan
        return d
    s0 = H.perf_stats(r, 0.0)
    for k in keys:
        d[k] = s0[k]
    for bp in COSTS:
        d['mean_net%d' % bp] = float((r - bp / 10000.0).mean())
    s30 = H.perf_stats(r, COST_PRIMARY)
    d['pf_net30'] = s30['pf']
    d['win_net30'] = s30['win_rate']
    sh, mdd, wm, nm = _monthly_book(r, mth)
    d['sharpe'], d['mdd'] = sh, mdd
    d['worst_month'], d['n_month'] = wm, nm
    return d


def regime_matrix(trades, lg=None):
    """SPEC section 7 core matrix: one row per (Alpha, Regime).

    `trades` must carry columns alpha / ret / regime / month.
    """
    rows = []
    for a in sorted(trades['alpha'].unique()):
        t = trades[trades['alpha'] == a]
        for rg in list(RSTATES) + ['NA']:
            s = t[t['regime'] == rg]
            if len(s) == 0:
                continue
            d = cell_stats(s['ret'].to_numpy(), s['month'].to_numpy())
            d.update({'alpha': a, 'alpha_name': ALPHA_NAME.get(a, a),
                      'regime': rg, 'phase': phase_of(s['entry_date'].iloc[0])})
            rows.append(d)
    df = pd.DataFrame(rows)
    cols = (['alpha', 'alpha_name', 'regime', 'n', 'win_rate', 'mean',
             'median', 'pf', 't', 'sharpe', 'mdd', 'worst_month',
             'mean_net15', 'mean_net30', 'mean_net50', 'pf_net30',
             'win_net30', 'p05', 'p95', 'n_month'])
    df = df[[c for c in cols if c in df.columns]]
    if lg:
        lg('regime matrix rows %d' % len(df))
    return df


def dependence_test(trades, B=None, seed=None, lg=None):
    """Q1: does Alpha i perform differently across regimes?

    Spread = max - min regime mean net-30bp among cells with n >= min_cell_n.
    Null = permute the regime labels inside each alpha (B draws), which
    destroys the regime->return link while preserving both marginals.
    """
    B = B or PREREG_AOS['boot_B']
    seed = seed or PREREG_AOS['seed']
    min_n = PREREG_AOS['min_cell_n']
    rng = np.random.default_rng(seed)
    rows = []
    for a in sorted(trades['alpha'].unique()):
        t = trades[trades['alpha'] == a]
        r = (t['ret'].to_numpy(dtype='float64') - COST_PRIMARY / 10000.0)
        lab = t['regime'].to_numpy()
        ok = np.isfinite(r)
        r, lab = r[ok], lab[ok]
        states = [s for s in RSTATES if (lab == s).sum() >= min_n]
        means = {s: float(r[lab == s].mean()) for s in states}
        spread = (max(means.values()) - min(means.values())) if len(states) >= 2 else np.nan
        null = np.full(B, np.nan)
        if len(states) >= 2:
            # vectorised permutation: encode the state index once, then shuffle
            # that integer vector (O(n) per draw instead of O(n * states)).
            sid = np.full(r.size, -1, dtype='int8')
            for i, s in enumerate(states):
                sid[lab == s] = i
            v = sid >= 0
            rv, sv = r[v], sid[v]
            k = len(states)
            for b in range(B):
                p = rng.permutation(sv)
                cnt = np.bincount(p, minlength=k).astype('float64')
                sm = np.bincount(p, weights=rv, minlength=k)
                mm = sm / np.where(cnt > 0, cnt, np.nan)
                mm = mm[np.isfinite(mm)]
                if mm.size >= 2:
                    null[b] = mm.max() - mm.min()
        nn = null[np.isfinite(null)]
        p = float((nn >= spread).mean()) if (nn.size and np.isfinite(spread)) else np.nan
        row = {'alpha': a, 'alpha_name': ALPHA_NAME.get(a, a),
               'n_trades': int(r.size), 'n_states_ge_%d' % min_n: len(states),
               'spread_net30': spread,
               'null_mean': float(nn.mean()) if nn.size else np.nan,
               'null_p95': float(np.percentile(nn, 95)) if nn.size else np.nan,
               'p_perm': p,
               'significant_5pct': bool(np.isfinite(p) and p < 0.05)}
        for s in RSTATES:
            row['mean_net30_%s' % s] = means.get(s, np.nan)
            row['n_%s' % s] = int((lab == s).sum())
        rows.append(row)
        if lg:
            lg('  Q1 %s n=%d spread %.4f  null_p95 %.4f  p=%.3f %s'
               % (a, r.size, spread if np.isfinite(spread) else np.nan,
                  row['null_p95'] if np.isfinite(row['null_p95']) else np.nan,
                  p if np.isfinite(p) else np.nan,
                  '<5%' if row['significant_5pct'] else ''))
    return pd.DataFrame(rows)


def regime_oos_information(trades, labels, dates, B=None, seed=None, lg=None):
    """Q3 -- can the CURRENT regime pick the better Alpha in OOS?

    Protocol: rank alphas inside each regime on the IS window only
    (mean net-30bp, n >= min_cell_n), map each regime to its Top-2 alphas,
    then on the OOS window take every trade whose alpha is in its regime's
    Top-2 ("regime-selected") versus every trade ("all").  The null keeps the
    same per-regime alpha counts but permutes which alphas are selected.

    Returns a summary row per window plus the IS map (never re-fitted on OOS).
    """
    B = B or PREREG_AOS['boot_B']
    seed = seed or PREREG_AOS['seed']
    min_n = PREREG_AOS['min_cell_n']
    rng = np.random.default_rng(seed)
    t = trades.copy()
    t['net'] = t['ret'].astype('float64') - COST_PRIMARY / 10000.0
    t['phase'] = [phase_of(x) for x in t['entry_date']]

    is_t = t[t['phase'] == 'IS']
    grid = {}
    for rg in RSTATES:
        s = is_t[is_t['regime'] == rg]
        m = (s.groupby('alpha')['net'].agg(['mean', 'size']))
        m = m[m['size'] >= min_n]
        grid[rg] = list(m.sort_values('mean', ascending=False).index[:2])
    sel = np.zeros(len(t), dtype=bool)
    for rg, al in grid.items():
        sel |= (t['regime'].to_numpy() == rg) & t['alpha'].isin(al).to_numpy()

    out = {'is_map': grid, 'k': 2, 'min_cell_n': min_n}
    oos = (t['phase'] == 'OOS').to_numpy()
    r = t['net'].to_numpy()
    a_all = r[oos & np.isfinite(r)]
    a_sel = r[oos & sel & np.isfinite(r)]
    obs = float(a_sel.mean() - a_all.mean()) if (a_sel.size and a_all.size) else np.nan

    # null: inside each (regime) keep the number of selected alphas but draw
    # them at random from that regime's IS alpha pool
    pools = {rg: list(is_t.loc[is_t['regime'] == rg, 'alpha'].unique())
             for rg in RSTATES}
    null = np.full(B, np.nan)
    oos_alpha = t.loc[oos, 'alpha'].to_numpy()
    oos_reg = t.loc[oos, 'regime'].to_numpy()
    oos_r = r[oos]
    # integer encodings so the null can be drawn with one boolean gather per
    # draw instead of a python membership test per trade.
    alphas = sorted(t['alpha'].unique())
    aidx = np.searchsorted(np.array(alphas), oos_alpha)
    ridx = np.array([RSTATES.index(x) if x in RSTATES else -1 for x in oos_reg])
    pool_idx = {ri: np.searchsorted(np.array(alphas), np.array(pools.get(rg, [])))
                for ri, rg in enumerate(RSTATES)}
    rid_ok = ridx >= 0
    oos_mean = float(np.nanmean(oos_r)) if np.isfinite(oos_r).any() else np.nan
    for b in range(B):
        pick = np.zeros((len(RSTATES), len(alphas)), dtype=bool)
        for ri, rg in enumerate(RSTATES):
            pi = pool_idx[ri]
            if pi.size >= 2:
                pick[ri, rng.choice(pi, size=min(2, pi.size), replace=False)] = True
            elif pi.size:
                pick[ri, pi] = True
        m = np.zeros(len(aidx), dtype=bool)
        m[rid_ok] = pick[ridx[rid_ok], aidx[rid_ok]]
        if m.any() and np.isfinite(oos_r[m]).any():
            null[b] = float(np.nanmean(oos_r[m]) - oos_mean)
    nn = null[np.isfinite(null)]
    out.update({'oos_n_all': int(a_all.size), 'oos_n_selected': int(a_sel.size),
                'oos_mean_all_net30': float(a_all.mean()) if a_all.size else np.nan,
                'oos_mean_selected_net30': float(a_sel.mean()) if a_sel.size else np.nan,
                'obs_diff': obs,
                'null_mean': float(nn.mean()) if nn.size else np.nan,
                'null_p95': float(np.percentile(nn, 95)) if nn.size else np.nan,
                'p_perm': float((nn >= obs).mean()) if (nn.size and np.isfinite(obs)) else np.nan})
    out['oos_information'] = bool(np.isfinite(out['p_perm']) and out['p_perm'] < 0.05)
    if lg:
        lg('  Q3 IS map %s' % grid)
        lg('  Q3 OOS selected %.4f vs all %.4f  diff %s  p=%.3f -> %s'
           % (out['oos_mean_selected_net30'], out['oos_mean_all_net30'],
              fmt(obs), out['p_perm'] if np.isfinite(out['p_perm']) else np.nan,
              'HAS INFO' if out['oos_information'] else 'NO INFO'))
    return out


def regression_frame(labels, dates):
    """(N,) label array -> tidy frame for the regime series artefact."""
    return pd.DataFrame({'trade_date': dates, 'regime': labels})


def regime_market_context(feat, lab, dates):
    """SPEC section 4.4 -- descriptive market state of each regime.

    `fwd1` is the NEXT-session HS300 return; it is used only to DESCRIBE the
    regime ex post (report table), never by any selection rule.
    """
    cl = np.asarray(feat['close'], dtype='float64')
    fwd1 = np.full(cl.size, np.nan)
    fwd1[:-1] = cl[1:] / cl[:-1] - 1.0
    lab = np.asarray(lab)
    rows = []
    for s in list(RSTATES) + ['NA']:
        m = lab == s
        if not m.any():
            continue
        rs = run_stats(lab, valid=(lab != 'NA')) if s != 'NA' else None
        rows.append({
            'regime': s, 'n_days': int(m.sum()),
            'share': float(m.mean()),
            'mean_fwd1': float(np.nanmean(fwd1[m])) if np.isfinite(fwd1[m]).any() else np.nan,
            'mean_r20': float(np.nanmean(np.asarray(feat['r20'])[m])),
            'mean_vol20': float(np.nanmean(np.asarray(feat['vol20'])[m])),
            'mean_breadth': float(np.nanmean(np.asarray(feat['breadth'])[m])),
            'mean_ln_dn': float(np.nanmean(np.asarray(feat['ln_dn'])[m])),
            'mean_liq': float(np.nanmean(np.asarray(feat['liq'])[m])),
            'below_min_share': bool(m.mean() < PREREG_AOS['regime_min_share']),
        })
        if rs:
            rows[-1]['transitions_into'] = int((np.asarray(lab[1:])[np.asarray(lab[1:]) == s]).size)
    return pd.DataFrame(rows)


def regime_phase_mix(lab, dates):
    """Regime share inside each IS / VALID / OOS phase."""
    ph = np.array([phase_of(d) for d in dates])
    lab = np.asarray(lab)
    rows = []
    for p in ('IS', 'VALID', 'OOS', 'PRE', 'OUT'):
        m = ph == p
        if not m.any():
            continue
        tot = int(m.sum())
        r = {'phase': p, 'n_days': tot}
        for s in RSTATES:
            r[s + '_n'] = int((lab[m] == s).sum())
            r[s + '_share'] = float((lab[m] == s).mean())
        rows.append(r)
    return pd.DataFrame(rows)


def write_regime_series(labels, dates, feat, thr, path=None):
    """SPEC section 4.2 -- the locked thresholds travel with the series."""
    df = pd.DataFrame({'trade_date': dates, 'regime': labels})
    for k in ('close', 'ma20', 'ma30', 'ma60', 'r20', 'r60', 'vol20',
              'breadth', 'ln_dn', 'liq'):
        df[k] = np.asarray(feat[k])
    for k in PREREG_AOS['regime_quantiles']:
        df['thr_' + k] = float(thr[k])
    df['thr_is_window'] = '%s..%s' % tuple(thr['_window'])
    df['phase'] = [phase_of(d) for d in dates]
    return save_csv(df, path or 'aos_regime_series.csv'), thr


if __name__ == '__main__':
    lg = Log('aos_common_selftest')
    lg.sep('=')
    lg('H-AOS-01 %s v%s' % (PREREG_AOS['hypothesis_id'], PREREG_AOS['version']))
    lg('SPEC sha256 %s' % sha256(SPEC)[:16])
    lg.sep('-')
    panel = load_panel(lg=lg)
    g, el = panel['g'], panel['el']
    lab, feat, thr = regime_series(g, el, lg=lg)
    lg.sep('-')
    st, base_rs, weak = regime_stability(feat, g['dates'], thr, lg=lg)
    lg('stability -> %s' % ('WEAK' if weak else 'STABLE'))
    lg.sep('-')
    core, ext, ext_meta = build_alphas(panel, lg=lg)
    lg('core trades %d  alphas %s' % (len(core), sorted(core['alpha'].unique())))
    for k, v in ext_meta.items():
        lg('ext %s %s' % (k, v.get('status')))
    core = attach_regime(core, lab, g['dates'])
    core['phase'] = [phase_of(x) for x in core['entry_date']]
    lg.sep('-')
    m = regime_matrix(core, lg=lg)
    save_csv(m, '_selftest_matrix.csv')
    dep = dependence_test(core, B=200, lg=lg)
    save_csv(dep, '_selftest_dependence.csv')
    lg.sep('-')
    lg('trade mix by alpha/phase:')
    lg(core.groupby(['alpha', 'phase']).size().to_string())
    lg('selftest OK')
