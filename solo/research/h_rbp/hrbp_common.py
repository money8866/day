# -*- coding: utf-8 -*-
"""H-RBP-01 common layer (independent experiment module).

Research ID : H-RBP-01
Title       : High-Volume Event -> real-time retracement -> re-strength
Hypothesis  : the question is NOT whether HVE itself predicts anything (V1
              already falsified that) but whether a *real-time observable*
              retracement after the anchor event, with structure preserved
              and a real-time re-strength trigger, carries incremental
              tradable alpha.

Discipline (see H_RBP_01_RESEARCH_SPEC.md)
    * Everything is pre-registered in PREREG_H before any result is seen.
    * No look-ahead.  Every signal condition uses data <= signal_date only.
    * Entry is always T+1 OPEN; the signal-day close is never the primary
      friction-free fill.
    * One event -> at most one trade.
    * This module only READS the verified HVE data layer (hve_common).  It
      never modifies research/hve/* or the shared Tushare cache.
"""
import os
import sys
import json
import time
import warnings

import numpy as np
import pandas as pd

np.seterr(divide='ignore', invalid='ignore')
warnings.filterwarnings('ignore', message='All-NaN slice encountered')
warnings.filterwarnings('ignore', message='Mean of empty slice')

HERE = os.path.dirname(os.path.abspath(__file__))
HVE_DIR = os.path.abspath(os.path.join(HERE, '..', 'hve'))
if HVE_DIR not in sys.path:
    sys.path.insert(0, HVE_DIR)

import hve_common as H          # noqa: E402  -- read-only reuse of the data layer

OUT = HERE
DATA = os.path.join(OUT, 'data')
os.makedirs(DATA, exist_ok=True)

GRID_CACHE = os.path.join(DATA, 'hrbp_grid.npz')
EV_CACHE = os.path.join(DATA, 'hrbp_events.parquet')
ELIG_CACHE = os.path.join(DATA, 'hrbp_elig.npy')
CACHE_VERSION = 1

# =====================================================================
# PREREG_H -- frozen before any H-RBP-01 result was observed
# =====================================================================
PREREG_H = {
    'hypothesis_id': 'H-RBP-01',
    'version': '1.0',
    'created': '2026-09-30',
    'title': 'High-Volume Event -> retracement entry (RBP)',

    # ---- sample -------------------------------------------------------
    'study_start': '20190101',
    'exclude_bse': True,           # BSE is out of scope for the pre-registered
                                   # tradable universe
    'min_list_days': 250,          # inherited from hve_common.eligibility
    'long_suspension_days': 60,
    'phase_IS': (2019, 2022),
    'phase_VALID': (2023, 2024),
    'phase_OOS': (2025, 2026),

    # ---- section 4 event anchor --------------------------------------
    'anchor': 'H_RBP_EVENT',
    'anchor_rule': 'VR20_excl >= 2.00 AND r1 >= 0.02 AND Close > Open '
                   'AND CLV >= 0.65',
    'anchor_vr20_min': 2.00,
    'anchor_ret_min': 0.02,
    'anchor_clv_min': 0.65,
    'anchor_vr20_excludes_today': True,
    'anchor_vr20_note': 'VR20_excl = Volume[t] / mean(Volume[t-20 : t-1]); '
                        'the denominator EXCLUDES the event session, exactly '
                        'as the spec writes it.  This differs from hve_common '
                        'ind[VR20] (whose denominator contains t) -- that is '
                        'intentional and this study is NOT a reproduction of '
                        'HVE-Research V1.',
    'anchor_cluster_gap': 5,
    'anchor_cluster_anchor': 'max_amount',

    # ---- section 5 observation window --------------------------------
    'window': 60,                  # sessions t0+1 .. t0+60
    'lookback': 6,                 # extra left padding so every re-strength
                                   # lookback (max 5 sessions) stays inside the
                                   # gathered block

    # ---- section 6 retracement ---------------------------------------
    'bands': (('R1', 0.03, 0.05), ('R2', 0.05, 0.08),
              ('R3', 0.08, 0.12), ('R4', 0.12, 0.15)),
    'band_rule': 'first session t in [t0+1, t0+60] with '
                 'drawdown(t) in [-hi, -lo)',
    'drawdown_rule': 'Close[t] / max(High[t0..t]) - 1, expanding max only',
    'grid_thr_pct': (3, 5, 8, 10, 12, 15),
    'grid_rule': 'first session t with drawdown(t) <= -thr/100',

    # ---- section 7 structure -----------------------------------------
    'struct_keys': ('S1_MA20', 'S2_EVENT_LOW', 'S3_MA20_AND_EVENT_LOW',
                    'S4_RECENT_SWING_LOW'),
    'struct_grid': ('S1_MA20', 'S2_EVENT_LOW', 'S3_MA20_AND_EVENT_LOW'),
    'struct_at_grid': ('RETRACE', 'SIGNAL'),
    's4_pivot_k': 3,
    's4_rule': 'pivot low at j  <=>  Low[j] == min(Low[j-3 .. j+3]); '
               'confirmed at j+3; struct_low(t) = max Low[j] over '
               't0 <= j <= t-3 that are pivot lows (Low[t0] if none)',

    # ---- section 8 re-strength ---------------------------------------
    'rs_keys': ('RS1_3D', 'RS2_5D', 'RS3_MA20'),
    'rs_rules': {
        'RS1_3D': 'Close[t] > max(High[t-3 .. t-1])',
        'RS2_5D': 'Close[t] > max(High[t-5 .. t-1])',
        'RS3_MA20': 'Close[t] > MA20[t] and Close[t] > Close[t-1]',
    },
    'volconf_ratio': 1.00,
    'volconf_rule': 'Volume[t] / mean(Volume[t-20 : t-1]) >= 1.0',

    # ---- section 9 signal / entry / exit -----------------------------
    'entry_rule': 'entry_idx = signal_date + 1, entry_price = Open[entry_idx]',
    'horizons': (3, 5, 10, 20),
    'primary_horizon': 10,
    'exit_rule': 'Close[entry_idx + h] / Open[entry_idx] - 1, no stop',
    'primary_cell': {'band': 'R1', 'struct': 'S3_MA20_AND_EVENT_LOW',
                     'struct_at': 'RETRACE', 'rs': 'RS2_5D',
                     'volconf': False},
    'primary_cell_note': 'frozen before any result: R1 is the shallowest '
                         'pre-registered band (largest N, least depth '
                         'selection); S3 is the conjunction the task text '
                         'describes ("structure not damaged"); RS2 mirrors '
                         'the 5-day high used by the HVE V1 second-wave work.',

    # ---- costs / statistics ------------------------------------------
    'cost_bp': (0, 10, 20, 30, 50),
    'primary_cost_bp': 30,
    'n_perm': 1000,
    'seed': 20261101,
    'fdr_q': 0.05,
    'tail_levels': (0.01, 0.05, 0.10),
    'boot_B': 1000,
    'min_events': 30,
    'min_cell_events': 100,

    # ---- walk forward -------------------------------------------------
    'wf_train_years': 3,
    'wf_valid_years': 1,
    'wf_test_years': 1,
    'wf_start_year': 2019,
    'wf_end_year': 2026,

    # ---- regime (inherited verbatim from the V1 pre-registration) -----
    'regime_rule': H.PREREG['regime_rule'],
    'regime_index': '000300.SH',

    'trading_authorization': 'NO',
}


def phase_of_year(y):
    a, b = PREREG_H['phase_IS']
    if a <= y <= b:
        return 'IS'
    a, b = PREREG_H['phase_VALID']
    if a <= y <= b:
        return 'VALID'
    a, b = PREREG_H['phase_OOS']
    if a <= y <= b:
        return 'OOS'
    return 'PRE' if y < PREREG_H['phase_IS'][0] else 'OUT'


# =====================================================================
# logging
# =====================================================================
class Log(object):
    def __init__(self, name):
        self.name = name
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


# =====================================================================
# panel helpers
# =====================================================================
def board_ok(g):
    """(S,) bool -- universe layers of this study (BSE excluded)."""
    if not PREREG_H['exclude_bse']:
        return np.ones(len(g['codes']), dtype=bool)
    return g['board'] != 'BSE'


def eligibility_h(g, lg=None):
    """eligibility(g) AND the study's board filter; cached on disk."""
    if os.path.exists(ELIG_CACHE):
        e = np.load(ELIG_CACHE)
        if e.shape == g['close'].shape:
            if lg:
                lg('eligibility loaded from cache  %.4f' % float(e.mean()))
            return e
    e = H.eligibility(g) & board_ok(g)[:, None]
    np.save(ELIG_CACHE, e)
    if lg:
        lg('eligibility built & cached  %.4f' % float(e.mean()))
    return e


def vr20_excl_panel(g):
    """(S,N) float32 -- Volume[t] / mean(Volume[t-20 : t-1]).

    The denominator excludes the current session, per the spec.  The shared
    H._roll_mean is trailing-inclusive, so the series is shifted one session
    to the right before the ratio is taken.
    """
    m = H._roll_mean(g['vol'], 20)
    m_prev = np.full(m.shape, np.nan, dtype='float64')
    m_prev[:, 1:] = m[:, :-1]
    with np.errstate(invalid='ignore'):
        out = np.where(m_prev > 0, g['vol'].astype('float64') / m_prev, np.nan)
    return out.astype('float32')


def pivot_low_panel(g, k=3):
    """(S,N) bool -- confirmable pivot lows (Low[j] == min(Low[j-k .. j+k]))."""
    w = 2 * k + 1
    low = pd.DataFrame(g['low'].astype('float64').T)
    mn = low.rolling(w, center=True, min_periods=w).min()
    out = (low.to_numpy() == mn.to_numpy())
    return np.ascontiguousarray(out.T)


def gather_win(M, si, di, back, fwd, fill=np.nan):
    """(E, back+fwd+1) block of M for sessions t0-back .. t0+fwd.

    Column w holds session (t0 - back + w); cells outside the panel are
    replaced by `fill`, and the returned mask says which cells are real.
    """
    N = M.shape[1]
    off = np.arange(-int(back), int(fwd) + 1)
    cols = np.asarray(di)[:, None] + off[None, :]
    ok = (cols >= 0) & (cols <= N - 1)
    cc = np.clip(cols, 0, N - 1)
    v = np.asarray(M)[np.asarray(si)[:, None], cc]
    if np.issubdtype(np.asarray(M).dtype, np.bool_):
        return np.where(ok, v, fill).astype(bool), ok
    return np.where(ok, v, fill).astype('float32'), ok


def ret_from_open(g, si, ei, h):
    """Close[ei+h] / Open[ei] - 1  (NaN when the window leaves the panel)."""
    N = g['close'].shape[1]
    ei = np.asarray(ei).astype('int64')
    j = ei + int(h)
    ok = (ei >= 0) & (ei <= N - 1) & (j >= 0) & (j <= N - 1)
    a = np.clip(ei, 0, N - 1)
    b = np.clip(j, 0, N - 1)
    s = np.asarray(si)
    p0 = g['open'][s, a].astype('float64')
    p1 = g['close'][s, b].astype('float64')
    r = np.where((p0 > 0) & np.isfinite(p1), p1 / p0 - 1.0, np.nan)
    return np.where(ok, r, np.nan)


def ret_from_close(g, si, ei, h):
    """Close[ei+h] / Close[ei] - 1 -- secondary, audit-only convention."""
    N = g['close'].shape[1]
    ei = np.asarray(ei).astype('int64')
    j = ei + int(h)
    ok = (ei >= 0) & (ei <= N - 1) & (j >= 0) & (j <= N - 1)
    a = np.clip(ei, 0, N - 1)
    b = np.clip(j, 0, N - 1)
    s = np.asarray(si)
    p0 = g['close'][s, a].astype('float64')
    p1 = g['close'][s, b].astype('float64')
    r = np.where((p0 > 0) & np.isfinite(p1), p1 / p0 - 1.0, np.nan)
    return np.where(ok, r, np.nan)


def first_from(flag, start):
    """First column index >= start where flag is True; -1 when none.

    flag (E,K) bool, start (E,) int.  Used for every 'first qualifying
    session' rule so the timing convention cannot drift between modules.
    """
    E, K = flag.shape
    cols = np.arange(K)[None, :]
    s = np.asarray(start).astype('int64')[:, None]
    m = flag & (cols >= s) & (s >= 0)
    has = m.any(axis=1)
    pos = np.where(has, np.argmax(m, axis=1), -1).astype('int16')
    return pos


# =====================================================================
# regime
# =====================================================================
def regime_by_day(g, lg=None):
    """(N,) array of 'BULL'/'BEAR'/'RANGE' per session, from CSI300.

    Rule inherited verbatim from the V1 pre-registration; only the index
    source path is new (index_panel.parquet is part of the existing cache).
    """
    p = os.path.join(H.FS_DATA, 'index_panel.parquet')
    idx = pd.read_parquet(p)
    idx['trade_date'] = idx['trade_date'].astype(str)
    idx = idx[idx['ts_code'].astype(str) == PREREG_H['regime_index']]
    idx = idx.sort_values('trade_date')
    kmap = {d: i for i, d in enumerate(g['dates'])}
    z = np.full(len(g['dates']), np.nan)
    k = idx['trade_date'].map(kmap).to_numpy()
    v = pd.to_numeric(idx['close'], errors='coerce').to_numpy()
    m = np.isfinite(k) & np.isfinite(v)
    z[k[m].astype(int)] = v[m]
    z = pd.Series(z).ffill().to_numpy()
    ma60 = pd.Series(z).rolling(60, min_periods=40).mean().to_numpy()
    r60 = np.full(len(z), np.nan)
    r60[60:] = z[60:] / z[:-60] - 1.0
    reg = np.where((z > ma60) & (r60 > 0.05), 'BULL',
                   np.where((z < ma60) & (r60 < -0.05), 'BEAR', 'RANGE'))
    reg = np.where(np.isfinite(z) & np.isfinite(ma60) & np.isfinite(r60),
                   reg, 'RANGE').astype(object)
    if lg:
        for nm in ('BULL', 'RANGE', 'BEAR'):
            lg('  regime %-5s %d sessions' % (nm, int((reg == nm).sum())))
    return reg.astype(str)


# =====================================================================
# statistics helpers
# =====================================================================
def cost_ladder(r, cost_bp):
    return np.asarray(r, dtype='float64') - cost_bp / 10000.0


def tail_table(r, levels=(0.01, 0.05, 0.10)):
    """Full vs leave-top-k% mean, plus the top-5% profit share."""
    x = np.asarray(r, dtype='float64')
    x = x[np.isfinite(x)]
    out = {'n': int(x.size), 'mean_full': np.nan, 'leave_top_1pct': np.nan,
           'leave_top_5pct': np.nan, 'leave_top_10pct': np.nan,
           'tail_share_5pct': np.nan}
    if x.size == 0:
        return out
    out['mean_full'] = float(x.mean())
    s = np.sort(x)
    for lev in levels:
        k = max(1, int(round(x.size * lev)))
        keep = s[:x.size - k] if k < x.size else s[:0]
        out['leave_top_%dpct' % int(round(lev * 100))] = (
            float(keep.mean()) if keep.size else np.nan)
    gp = float(s[s > 0].sum())
    k5 = max(1, int(round(x.size * 0.05)))
    out['tail_share_5pct'] = (float(s[-k5:][s[-k5:] > 0].sum()) / gp
                              if gp > 0 else np.nan)
    return out


def cluster_boot_mean(r, months, B=1000, seed=20261101):
    """95% CI for the mean, resampling whole calendar months.

    Sums and counts are folded per cluster first, so a bootstrap replicate is
    a pick of clusters, not a rebuild of the sample -- the estimator is the
    same and it stays cheap at ~250k trades.
    """
    r = np.asarray(r, dtype='float64')
    m = np.asarray(months)
    ok = np.isfinite(r)
    r, m = r[ok], m[ok]
    if r.size == 0:
        return np.nan, np.nan, np.nan
    uq, inv = np.unique(m, return_inverse=True)
    cnt = np.bincount(inv, minlength=len(uq)).astype('float64')
    ssum = np.bincount(inv, weights=r, minlength=len(uq))
    rng = np.random.default_rng(seed)
    k = len(uq)
    draws = rng.integers(0, k, size=(B, k))
    c = cnt[draws].sum(axis=1)
    s = ssum[draws].sum(axis=1)
    means = np.where(c > 0, s / np.maximum(c, 1e-9), np.nan)
    return (float(r.mean()), float(np.nanpercentile(means, 2.5)),
            float(np.nanpercentile(means, 97.5)))


def cluster_boot_diff(ra, ma, rb, mb, B=1000, seed=20261101):
    """95% CI for mean(a) - mean(b), resampling calendar months jointly."""
    ra, ma = np.asarray(ra, 'float64'), np.asarray(ma)
    rb, mb = np.asarray(rb, 'float64'), np.asarray(mb)
    oa, ob = np.isfinite(ra), np.isfinite(rb)
    ra, ma = ra[oa], ma[oa]
    rb, mb = rb[ob], mb[ob]
    if ra.size == 0 or rb.size == 0:
        return np.nan, np.nan, np.nan
    uq = np.union1d(np.unique(ma), np.unique(mb))
    pos = {v: i for i, v in enumerate(uq)}
    ka = np.array([pos[v] for v in ma])
    kb = np.array([pos[v] for v in mb])
    k = len(uq)
    ca = np.bincount(ka, minlength=k).astype('float64')
    cb = np.bincount(kb, minlength=k).astype('float64')
    sa = np.bincount(ka, weights=ra, minlength=k)
    sb = np.bincount(kb, weights=rb, minlength=k)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, k, size=(B, k))
    acnt = ca[draws].sum(axis=1)
    bc = cb[draws].sum(axis=1)
    asum = sa[draws].sum(axis=1)
    bsum = sb[draws].sum(axis=1)
    ma_ = np.where(acnt > 0, asum / np.maximum(acnt, 1e-9), np.nan)
    mb_ = np.where(bc > 0, bsum / np.maximum(bc, 1e-9), np.nan)
    d = ma_ - mb_
    obs = float(ra.mean() - rb.mean())
    return obs, float(np.nanpercentile(d, 2.5)), float(np.nanpercentile(d, 97.5))


def block_boot_mean(r, block=20, B=1000, seed=20261101):
    """Stationary block bootstrap CI for the mean (order = calendar order)."""
    r = np.asarray(r, dtype='float64')
    r = r[np.isfinite(r)]
    n = r.size
    if n < 5:
        return np.nan, np.nan, np.nan
    rng = np.random.default_rng(seed + 7)
    nb = int(np.ceil(n / block))
    starts = rng.integers(0, n, size=(B, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    means = r[idx.reshape(B, -1)[:, :n]].mean(axis=1)
    return (float(r.mean()), float(np.percentile(means, 2.5)),
            float(np.percentile(means, 97.5)))


def portfolio_mdd(r, entry_idx, h, dates):
    """Max drawdown of an equal-weight, monthly-rebalanced book.

    Trades entered in the same calendar month are held together with weight
    1/N each for h sessions; the monthly return is the mean of the trade
    returns booked in that month.  The equity curve is the cumulative product
    across months.  A simple, explicitly-stated convention, not a tick-level
    accounting of overlapping positions.
    """
    r = np.asarray(r, 'float64')
    e = np.asarray(entry_idx)
    ok = np.isfinite(r) & (e >= 0)
    if ok.sum() == 0:
        return np.nan, np.nan
    ym = np.array([str(dates[i])[:6] if 0 <= i < len(dates) else ''
                   for i in e[ok]])
    s = pd.Series(r[ok]).groupby(ym).mean().sort_index()
    curve = (1.0 + s).cumprod()
    run = curve.cummax()
    mdd = float((curve / run - 1.0).min())
    return mdd, float(curve.iloc[-1]) if len(curve) else np.nan


def bh_fdr(pvals):
    return H.bh_fdr(pvals)


def save_csv(df, name):
    p = os.path.join(DATA, name) if not os.path.isabs(name) else name
    df.to_csv(p, index=False, encoding='utf-8-sig')
    return p


def save_json(obj, name):
    p = os.path.join(DATA, name) if not os.path.isabs(name) else name
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
    if x is None:
        return 'NA'
    v = float(x)
    if not np.isfinite(v):
        return 'nan'
    return ('%.' + str(nd) + 'f%%') % (100.0 * v)
