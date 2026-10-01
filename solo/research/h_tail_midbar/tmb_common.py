# -*- coding: utf-8 -*-
"""H-TAIL-MIDBAR-01 -- common layer.

Every rule in this module is transcribed from H_TAIL_MIDBAR_01_SPEC.md and was
frozen before any result was observed.  Nothing here may be edited after the
first run without invalidating the experiment (see H_TAIL_MIDBAR_01_FREEZE.md).

Scope (SPEC section 1): ONLY  "first >=3% bullish mid-bar at the close ->
sell next close".  No T+3/T+5/T+10/T+20, no HVT/W7/HVE, no second wave, no
retracement, no trend, no fundamentals, no theme, no ML, no RSI/MACD/KDJ, no
sentiment score, no composite scoring model.  MA5/MA20 and Volume are discrete
GATES only -- they never enter a continuous score.
"""
import os
import sys
import json
import time
import hashlib

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
HVE_DIR = os.path.join(ROOT, 'research', 'hve')
HRBP_DIR = os.path.join(ROOT, 'research', 'h_rbp')
HALPHA_DIR = os.path.join(ROOT, 'research', 'h_alpha')
for _p in (HVE_DIR, HRBP_DIR, HALPHA_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hve_common as H          # read-only dependency: panel + universe
import hrbp_common as R         # read-only dependency: tail / bootstrap / regime

OUT = HERE                      # every artefact lives in the study directory
SPEC = os.path.join(HERE, 'H_TAIL_MIDBAR_01_SPEC.md')
FREEZE = os.path.join(HERE, 'H_TAIL_MIDBAR_01_FREEZE.md')
LOG = os.path.join(HERE, 'tmb_run.log')

# SHA256 of the frozen SPEC -- must not change (SPEC section 25 / FREEZE section 1)
SPEC_SHA256 = '396329014C3233DC7BB31AA0B981CD577E140ED31D57E880AC3D56695BB4914D'

# =====================================================================
# PREREG_T -- frozen before any H-TAIL-MIDBAR-01 result was observed
# =====================================================================
PREREG_T = {
    'hypothesis_id': 'H-TAIL-MIDBAR-01',
    'version': '1.0',
    'created': '2026-10-01',
    'title': 'First >=3% bullish mid-bar at the close -> next-close exit',

    # ---- sample (SPEC section 17) ------------------------------------
    'study_start': '20220101',
    'burn_in_sessions': 40,        # padding kept before study_start so that
                                   # MA20 / slope5 / First(20) never see a
                                   # truncated window
    'phase_IS': (2022, 2024),
    'phase_VALID': (2025, 2025),
    'phase_OOS': (2026, 2026),

    # ---- event (SPEC section 3) --------------------------------------
    'thr_primary': 0.03,
    'thr_grid': (0.02, 0.03, 0.04, 0.05, 0.06, 0.07),
    'body_min': 0.03,
    'clv_min': 0.65,
    'body_rule': '(close - open) / open    [task text; NOT hve_common px.body]',
    'clv_rule': '(close - low) / (high - low)',

    # ---- first-event (SPEC section 4) --------------------------------
    'first_windows': (5, 10, 20),
    'first_primary': 10,
    'first_rule': 'First_w = E0 & ~any_prev(E0, w); window [t-w, t-1], today excluded',

    # ---- MA structures (SPEC section 5) ------------------------------
    'ma_fast': 5,
    'ma_slow': 20,
    'ma_slope': 5,
    'ma_structs': ('M1', 'M2', 'M3', 'M4'),
    'ma_primary': 'MApresent',     # M1 | M2 | M3 | M4

    # ---- volume gates (SPEC section 6) -------------------------------
    'vr_win': 20,
    'vr5_win': 5,
    'vr_note': 'VR20 = vol[t] / mean(vol[t-20 .. t-1]); denominator EXCLUDES '
               't; suspended sessions are NaN, not zero',
    'vr_gates': {'V0': None, 'V1': 1.20, 'V2': 1.50, 'V3': 2.00, 'V4': 3.00,
                 'V5': (1.20, 3.00)},
    'vr_primary': 'V5',

    # ---- arms (SPEC section 7) ---------------------------------------
    'arms': ('RAW', 'B', 'B+MA', 'B+VOL', 'B+MA+VOL'),
    'primary_arm': 'B+MA+VOL',

    # ---- execution / exit (SPEC sections 8, 9) -----------------------
    'entry_primary': 'close[t]',
    'entry_second': 'open[t+1]',
    'exit_primary': 'close[t+1]',
    'exit_diagnostics': ('open[t+1]', 'high[t+1]', 'low[t+1]'),

    # ---- statistics (SPEC sections 10, 11, 19, 20) -------------------
    'cost_bp': (0, 10, 20, 30, 50),
    'primary_cost_bp': 30,
    'tail_levels': (0.01, 0.05, 0.10),
    'boot_B': 1000,
    'null_B': 1000,
    'null_rounds': 8,
    'null_min_resolution': 0.99,
    'seed': 20261101,
    'min_n': 30,
    'overlap_windows': (1, 5, 20),

    # ---- parameter grid (SPEC section 22) ----------------------------
    'grid_ma': ('ALL', 'M1', 'M2', 'M3', 'M4'),
    'grid_vr': (None, 1.2, 1.5, 2.0, 3.0),

    # ---- regime (SPEC section 16) ------------------------------------
    'regime_index': '000300.SH',
    'regime_rule': 'inherited verbatim from hrbp_common.regime_by_day',

    'trading_authorization': 'NO',
}

SEED = PREREG_T['seed']
B_BOOT = PREREG_T['boot_B']
B_NULL = PREREG_T['null_B']
COSTS = PREREG_T['cost_bp']
COST_PRIMARY = PREREG_T['primary_cost_bp']
TAIL_LEVELS = PREREG_T['tail_levels']

MATKEYS = ('open', 'high', 'low', 'close', 'vol', 'amount', 'turnover',
           'pct_chg', 'traded', 'st', 'delist', 'limup', 'limdn', 'oneword')

MA_STRUCT_IDS = {'M1': 1, 'M2': 2, 'M3': 3, 'M4': 4}


# =====================================================================
# logging / io
# =====================================================================
class Log(object):
    def __init__(self, name='tmb_run'):
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


def save_csv(df, name):
    p = os.path.join(OUT, name)
    df.to_csv(p, index=False, encoding='utf-8-sig')
    return p


def save_json(obj, name):
    p = os.path.join(OUT, name)
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
    v = None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 'NA'
    if not np.isfinite(v):
        return 'nan'
    return ('%.' + str(nd) + 'f%%') % (100.0 * v)


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for blk in iter(lambda: f.read(1 << 20), b''):
            h.update(blk)
    return h.hexdigest().upper()


def verify_spec(lg=None):
    """Freeze guard: the SPEC must still be the file that was frozen."""
    got = sha256(SPEC)
    if got != SPEC_SHA256:
        msg = ('SPEC HASH MISMATCH\n  expected %s\n  actual   %s\n'
               'The pre-registration has been edited after the freeze; the '
               'experiment is void.' % (SPEC_SHA256, got))
        if lg:
            lg(msg)
        return False
    if lg:
        lg('SPEC hash verified (%s)' % got[:16])
    return True


# =====================================================================
# panel
# =====================================================================
def load_grid(lg=None):
    """Existing Tushare cache panel (read-only).  No external data source."""
    g = H.build_grid(lg)
    if lg:
        lg('panel S=%d N=%d  %s..%s'
           % (g['close'].shape[0], g['close'].shape[1],
              g['dates'][0], g['dates'][-1]))
    return g


def truncate(g, K):
    """Same panel with the last K sessions removed (look-ahead audit)."""
    N = g['close'].shape[1] - int(K)
    gt = dict(g)
    for k in MATKEYS:
        gt[k] = g[k][:, :N]
    gt['dates'] = g['dates'][:N]
    return gt


# =====================================================================
# rolling primitives -- time is axis 1 (hve_common._roll_mean convention)
# =====================================================================
def _roll(M, w, how='mean', mp=None):
    d = pd.DataFrame(np.asarray(M).T)
    roll = d.rolling(w, min_periods=(w if mp is None else mp))
    v = roll.mean() if how == 'mean' else roll.sum()
    return v.to_numpy().T


def _slope(M, w, lag):
    out = np.full(M.shape, np.nan, dtype='float32')
    out[:, lag:] = (M[:, lag:] - M[:, :-lag]) / float(w)
    return out


def first_mask(E0, w, forward=False):
    """First_w = E0 & ~any_prev(E0, w)  (SPEC section 4).

    Counts are taken with a cumulative sum so no (S,N) float64 rolling block is
    materialised.  `forward=True` builds the deliberately WRONG, future-looking
    variant used only by the future-information audit (SPEC section 21.6).
    """
    N = E0.shape[1]
    cs = np.cumsum(E0.astype('int32'), axis=1, dtype='int32')
    if forward:
        idx = np.clip(np.arange(N)[None, :] + int(w), 0, N - 1)
        cnt = np.take_along_axis(cs, idx, axis=1) - cs      # count[t+1 .. t+w]
        return E0 & (cnt == 0)
    cnt = np.zeros(cs.shape, dtype='int32')
    cnt[:, 1:] = cs[:, :-1]                                 # count[0 .. t-1]
    if N > w + 1:
        cnt[:, w + 1:] -= cs[:, :N - w - 1]                 # - count[0 .. t-w-1]
    hist = np.zeros(N, dtype=bool)
    hist[w:] = True
    return E0 & hist[None, :] & (cnt == 0)


def ma_masks(cl, ma5, ma20, s5, s20):
    """SPEC section 5.  Four non-exclusive structures, plus their union."""
    prev5 = np.full(ma5.shape, np.nan, dtype='float32')
    prev5[:, 1:] = ma5[:, :-1]
    with np.errstate(invalid='ignore'):
        M1 = (cl > ma5) & (ma5 > ma20)
        M2 = (cl > ma5) & (ma5 >= ma20) & (s5 > 0)
        M3 = (cl > ma5) & (cl > ma20) & (ma5 > ma20) & (s20 >= 0)
        M4 = (cl > ma5) & (cl > ma20) & (ma5 > prev5) & (s20 <= 0)
    anym = M1 | M2 | M3 | M4
    code = np.zeros(cl.shape, dtype='int8')
    code[M4] = MA_STRUCT_IDS['M4']
    code[M3] = MA_STRUCT_IDS['M3']
    code[M2] = MA_STRUCT_IDS['M2']
    code[M1] = MA_STRUCT_IDS['M1']          # priority M1 > M2 > M3 > M4
    return {'M1': M1, 'M2': M2, 'M3': M3, 'M4': M4}, anym, code


def ret_matrix(cl, h=1):
    """close[t+h] / close[t] - 1 over the whole slice (NaN when undefined)."""
    S, N = cl.shape
    out = np.full((S, N), np.nan, dtype='float32')
    if h < N:
        a = cl[:, :N - h]
        b = cl[:, h:]
        with np.errstate(invalid='ignore'):
            out[:, :N - h] = np.where((a > 0) & np.isfinite(a) & np.isfinite(b),
                                      b / a - 1.0, np.nan)
    return out


# =====================================================================
# state
# =====================================================================
class State(object):
    """Everything the experiment needs, restricted to the study window."""

    def __init__(self, g, lg=None, trunc=0):
        self.trunc = int(trunc)
        gg = truncate(g, trunc) if trunc else g
        N_full = gg['close'].shape[1]
        dates = gg['dates']
        i0 = int(np.searchsorted(dates, PREREG_T['study_start']))
        pad = int(PREREG_T['burn_in_sessions'])
        c0 = max(0, i0 - pad)
        self.c0, self.s0 = c0, i0 - c0
        sl = slice(c0, N_full)

        board = gg['board']
        el = H.eligibility(gg) & (board != 'BSE')[:, None]

        self.g = gg
        self.dates = dates
        self.dates_w = dates[sl]                 # study-window session labels
        self.codes = gg['codes']
        self.S, self.N = gg['close'].shape[0], N_full - c0
        self.sl = sl

        self.o = gg['open'][:, sl]
        self.hi = gg['high'][:, sl]
        self.lo = gg['low'][:, sl]
        self.cl = gg['close'][:, sl]
        self.pc = gg['pct_chg'][:, sl]
        self.traded = gg['traded'][:, sl]
        self.el = el[:, sl]

        volm = np.where(self.traded, gg['vol'][:, sl], np.nan).astype('float32')
        self.vol = volm

        # ---- MA structures ------------------------------------------
        self.ma5 = _roll(self.cl, PREREG_T['ma_fast']).astype('float32')
        self.ma20 = _roll(self.cl, PREREG_T['ma_slow']).astype('float32')
        lags = PREREG_T['ma_slope']
        self.s5 = _slope(self.ma5, lags, lags)
        self.s20 = _slope(self.ma20, lags, lags)
        self.M, self.MAANY, self.MAcode = ma_masks(
            self.cl, self.ma5, self.ma20, self.s5, self.s20)

        # ---- volume gates -------------------------------------------
        self.vr5 = self._vr(PREREG_T['vr5_win'])
        self.vr20 = self._vr(PREREG_T['vr_win'])
        self.V = self._gates()

        # ---- shape descriptors --------------------------------------
        hl = self.hi - self.lo
        with np.errstate(invalid='ignore'):
            self.body = np.where(self.o > 0, (self.cl - self.o) / self.o, np.nan)
            self.clv = np.where(hl > 0, (self.cl - self.lo) / hl, np.nan)

        self.Rg = ret_matrix(self.cl, 1)          # close[t] -> close[t+1]
        self._e0 = {}
        self._first = {}

        if lg:
            lg('state window %s..%s  cols=%d  burn-in=%d'
               % (self.dates_w[0], self.dates_w[-1], self.N, self.s0))
            lg('eligible density in window %.4f' % float(self.el.mean()))

    # ------------------------------------------------------------------
    def _vr(self, w):
        m = _roll(self.vol, w, mp=w)
        prev = np.full(m.shape, np.nan, dtype='float32')
        prev[:, 1:] = m[:, :-1]
        with np.errstate(invalid='ignore'):
            return np.where(prev > 0, self.vol / prev, np.nan).astype('float32')

    def _gates(self):
        vr = self.vr20
        out = {}
        for k, v in PREREG_T['vr_gates'].items():
            if v is None:
                out[k] = np.ones(self.cl.shape, dtype=bool)
            elif isinstance(v, tuple):
                out[k] = (vr >= v[0]) & (vr <= v[1])
            else:
                out[k] = (vr >= v)
        return out

    # ------------------------------------------------------------------
    def e0(self, thr):
        """Core event mask E0(thr) -- SPEC section 3."""
        key = round(float(thr), 6)
        if key in self._e0:
            return self._e0[key]
        with np.errstate(invalid='ignore'):
            m = (self.el & (self.pc >= thr) & (self.cl > self.o)
                 & (self.body >= PREREG_T['body_min'])
                 & (self.clv >= PREREG_T['clv_min']))
        m = np.ascontiguousarray(m & np.isfinite(self.pc))
        self._e0[key] = m
        return m

    def first(self, thr, w):
        key = (round(float(thr), 6), int(w))
        if key not in self._first:
            self._first[key] = first_mask(self.e0(thr), w)
        return self._first[key]

    def arm(self, thr=None, w=None, ma=None, vr=None):
        """Compose an arm mask from the frozen pieces."""
        thr = PREREG_T['thr_primary'] if thr is None else thr
        m = self.e0(thr)
        if w is not None:
            m = m & self.first(thr, w)
        if ma is not None:
            m = m & (self.MAANY if ma == 'MApresent' else self.M[ma])
        if vr is not None and vr != 'V0':
            m = m & self.V[vr]
        return m

    # ------------------------------------------------------------------
    def events(self, msk):
        """-> (s, t_abs, j_slice, r_primary) for a mask, study window only."""
        s, j = np.nonzero(msk)
        keep = (j >= self.s0) & (j <= self.N - 2)
        s, j = s[keep], j[keep]
        return s, (j + self.c0), j, self.Rg[s, j].astype('float64')

    def months(self, t_abs):
        return np.array([self.dates[int(t)][:6] for t in np.asarray(t_abs)],
                        dtype=object)

    def years(self, t_abs):
        return np.array([int(self.dates[int(t)][:4]) for t in np.asarray(t_abs)],
                        dtype='int32')


# =====================================================================
# return legs (never a T-day intraday price as a fill -- SPEC section 8)
# =====================================================================
def leg(g, si, ti, off, fnum, fden):
    """field_num[t+off] / field_den[t] - 1, NaN outside the panel."""
    N = g['close'].shape[1]
    si = np.asarray(si, dtype='int64')
    ti = np.asarray(ti, dtype='int64')
    j = ti + int(off)
    ok = (ti >= 0) & (j >= 0) & (j <= N - 1)
    a = np.clip(ti, 0, N - 1)
    b = np.clip(j, 0, N - 1)
    p0 = g[fden][si, a].astype('float64')
    p1 = g[fnum][si, b].astype('float64')
    with np.errstate(invalid='ignore'):
        r = np.where((p0 > 0) & np.isfinite(p0) & np.isfinite(p1),
                     p1 / p0 - 1.0, np.nan)
    return np.where(ok, r, np.nan)


# =====================================================================
# statistics
# =====================================================================
def empty_metrics():
    d = {'n': 0, 'mean': np.nan, 'median': np.nan, 'win': np.nan, 'pf': np.nan,
         'std': np.nan, 'max': np.nan, 'min': np.nan, 'p10': np.nan,
         'p25': np.nan, 'p75': np.nan, 'p90': np.nan,
         'win_net30': np.nan, 'pf30': np.nan,
         'leave_top_1pct': np.nan, 'leave_top_5pct': np.nan,
         'leave_top_10pct': np.nan, 'tail_share_5pct': np.nan,
         'leave_top_1pct_net30': np.nan, 'leave_top_5pct_net30': np.nan,
         'leave_top_10pct_net30': np.nan, 'tail_share_5pct_net30': np.nan,
         'top1_share': np.nan, 'top5_share': np.nan}
    for bp in COSTS:
        d['mean_net%d' % bp] = np.nan
    return d


def _share(x, lev):
    """Share of TOTAL positive gross return contributed by the top lev fraction."""
    s = np.sort(x)
    gp = float(s[s > 0].sum())
    if gp <= 0:
        return np.nan
    k = max(1, int(round(x.size * lev)))
    return float(s[-k:][s[-k:] > 0].sum()) / gp


def metrics(r, do_boot=False, months=None, B=B_BOOT, seed=SEED):
    """SPEC section 10 + 11.  Tail fields are given on BOTH the gross and the
    net-30bp series; the gates use the net-30bp series (the research question
    is stated net of cost)."""
    x0 = np.asarray(r, dtype='float64')
    ok = np.isfinite(x0)
    x = x0[ok]
    out = empty_metrics()
    if x.size == 0:
        return out
    out['n'] = int(x.size)
    out['mean'] = float(x.mean())
    out['median'] = float(np.median(x))
    out['win'] = float((x > 0).mean())
    gs = float(x[x > 0].sum())
    ls = float(-x[x < 0].sum())
    out['pf'] = (gs / ls) if ls > 0 else np.nan
    out['std'] = float(x.std(ddof=1)) if x.size > 1 else np.nan
    for q in (10, 25, 75, 90):
        out['p%d' % q] = float(np.percentile(x, q))
    out['max'] = float(x.max())
    out['min'] = float(x.min())

    xn = x - COST_PRIMARY / 10000.0
    gs2 = float(xn[xn > 0].sum())
    ls2 = float(-xn[xn < 0].sum())
    out['win_net30'] = float((xn > 0).mean())
    out['pf30'] = (gs2 / ls2) if ls2 > 0 else np.nan

    for bp in COSTS:
        out['mean_net%d' % bp] = float((x - bp / 10000.0).mean())

    t = R.tail_table(x, TAIL_LEVELS)
    out.update({k: t[k] for k in ('leave_top_1pct', 'leave_top_5pct',
                                  'leave_top_10pct', 'tail_share_5pct')})
    t2 = R.tail_table(xn, TAIL_LEVELS)
    out.update({'leave_top_1pct_net30': t2['leave_top_1pct'],
                'leave_top_5pct_net30': t2['leave_top_5pct'],
                'leave_top_10pct_net30': t2['leave_top_10pct'],
                'tail_share_5pct_net30': t2['tail_share_5pct']})
    out['top1_share'] = _share(x, 0.01)
    out['top5_share'] = _share(x, 0.05)

    if do_boot and x.size >= 2:
        mm = None if months is None else np.asarray(months)[ok]
        b = boot_mean(xn, mm, B=B, seed=seed)
        out.update({'boot_mean': b['mean'], 'boot_lo': b['lo'],
                    'boot_hi': b['hi'], 'boot_p_le0': b['p_le0'],
                    'boot_n_month': b['n_month']})
    return out


def boot_mean(r, months, B=B_BOOT, seed=SEED):
    """Monthly cluster bootstrap of the mean (SPEC section 19)."""
    x = np.asarray(r, dtype='float64')
    m = np.asarray(months)
    ok = np.isfinite(x)
    x, m = x[ok], m[ok]
    res = dict(mean=np.nan, lo=np.nan, hi=np.nan, p_le0=np.nan,
               p_ge0=np.nan, n_month=0, n=int(x.size))
    if x.size < 2:
        return res
    uq, inv = np.unique(m, return_inverse=True)
    cnt = np.bincount(inv, minlength=len(uq)).astype('float64')
    ssum = np.bincount(inv, weights=x, minlength=len(uq))
    rng = np.random.default_rng(seed)
    k = len(uq)
    draws = rng.integers(0, k, size=(B, k))
    c = cnt[draws].sum(axis=1)
    s = ssum[draws].sum(axis=1)
    means = np.where(c > 0, s / np.maximum(c, 1e-9), np.nan)
    means = means[np.isfinite(means)]
    if means.size == 0:
        return res
    res.update(mean=float(x.mean()), lo=float(np.percentile(means, 2.5)),
               hi=float(np.percentile(means, 97.5)),
               p_le0=float((means <= 0).mean()),
               p_ge0=float((means >= 0).mean()), n_month=int(k))
    return res


def diff_boot(ra, ma, rb, mb, B=B_BOOT, seed=SEED):
    """Paired-free monthly-cluster bootstrap of mean(a) - mean(b),
    with the replicate distribution kept so a p-value is available."""
    ra = np.asarray(ra, 'float64')
    rb = np.asarray(rb, 'float64')
    ma, mb = np.asarray(ma), np.asarray(mb)
    oa, ob = np.isfinite(ra), np.isfinite(rb)
    ra, ma = ra[oa], ma[oa]
    rb, mb = rb[ob], mb[ob]
    out = dict(obs=np.nan, lo=np.nan, hi=np.nan, p_ge0=np.nan, p_le0=np.nan,
               n_month=0, n_a=int(ra.size), n_b=int(rb.size))
    if ra.size < 2 or rb.size < 2:
        return out
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
    ac = ca[draws].sum(1)
    bc = cb[draws].sum(1)
    asum = sa[draws].sum(1)
    bsum = sb[draws].sum(1)
    a_ = np.where(ac > 0, asum / np.maximum(ac, 1e-9), np.nan)
    b_ = np.where(bc > 0, bsum / np.maximum(bc, 1e-9), np.nan)
    d = (a_ - b_)
    d = d[np.isfinite(d)]
    out.update(obs=float(ra.mean() - rb.mean()),
               lo=float(np.percentile(d, 2.5)) if d.size else np.nan,
               hi=float(np.percentile(d, 97.5)) if d.size else np.nan,
               p_ge0=float((d >= 0).mean()) if d.size else np.nan,
               p_le0=float((d <= 0).mean()) if d.size else np.nan,
               n_month=int(k))
    return out


# =====================================================================
# null models (SPEC section 14)
# =====================================================================
def ret_bucket(pc):
    """3-4% / 4-5% / 5-7% / >7%  (values in decimal)."""
    out = np.full(pc.shape, -1, dtype='int8')
    v = pc
    out[(v >= 0.03) & (v < 0.04)] = 0
    out[(v >= 0.04) & (v < 0.05)] = 1
    out[(v >= 0.05) & (v < 0.07)] = 2
    out[v >= 0.07] = 3
    return out


def vr_bucket(vr):
    out = np.full(vr.shape, 4, dtype='int8')      # 4 = missing
    ok = np.isfinite(vr)
    out[ok & (vr < 1.2)] = 0
    out[ok & (vr >= 1.2) & (vr < 1.5)] = 1
    out[ok & (vr >= 1.5) & (vr < 2.0)] = 2
    out[ok & (vr >= 2.0)] = 3
    return out


def build_pools(cells, keys):
    """Group the flat cell ids by key -> (flat, lo, hi, uq) ragged block."""
    order = np.argsort(keys, kind='stable')
    cf = np.ascontiguousarray(cells[order])
    kf = keys[order]
    uq, first = np.unique(kf, return_index=True)
    lo = first.astype('int64')
    hi = np.r_[first[1:], len(kf)].astype('int64')
    return cf, lo, hi, uq


def pool_lookup(uq, lo, hi, ev_key):
    pos = np.searchsorted(uq, ev_key)
    ok = pos < uq.size
    pp = np.clip(pos, 0, max(uq.size - 1, 0))
    ok &= (uq[pp] == ev_key) if uq.size else np.zeros(ev_key.size, bool)
    L = np.where(ok, lo[pp], 0)
    Hh = np.where(ok, hi[pp], 0)
    return L, Hh, ok


def draw_cells(L, Hh, flat, self_flat, rng, rounds):
    """One draw per event, never the event's own cell (the only forbidden cell)."""
    E = L.size
    out = np.full(E, -1, dtype='int64')
    todo = np.flatnonzero(Hh > L)
    for _ in range(int(rounds)):
        if todo.size == 0:
            break
        c = (Hh - L)[todo].astype('float64')
        p = (L[todo] + np.minimum((rng.random(todo.size) * c).astype('int64'),
                                  (c - 1).astype('int64')))
        v = flat[p]
        bad = (v == self_flat[todo])
        good = ~bad
        out[todo[good]] = v[good]
        todo = todo[bad]
    return out


def null_family(rflat, flat, lo, hi, uq, ev_key, ev_self, obs, B=B_NULL,
                seed=SEED, rounds=PREREG_T['null_rounds']):
    """One null family.  Returns the distribution summary and the resolution."""
    L, Hh, okkey = pool_lookup(uq, lo, hi, ev_key)
    L = np.where(okkey, L, 0)
    Hh = np.where(okkey, Hh, 0)
    rng = np.random.default_rng(seed)
    means = np.full(B, np.nan)
    res = np.zeros(B)
    with np.errstate(invalid='ignore'):
        for b in range(B):
            v = draw_cells(L, Hh, flat, ev_self, rng, rounds)
            good = v >= 0
            res[b] = float(good.mean())
            if good.any():
                means[b] = np.nanmean(rflat[v[good]])
    m = means[np.isfinite(means)]
    out = dict(obs=float(obs), B=int(B), resolution=float(res.mean()),
               key_coverage=float(okkey.mean()),
               null_mean=float(m.mean()) if m.size else np.nan,
               null_lo=float(np.percentile(m, 2.5)) if m.size else np.nan,
               null_hi=float(np.percentile(m, 97.5)) if m.size else np.nan,
               null_std=float(m.std(ddof=1)) if m.size > 1 else np.nan)
    out['excess'] = (out['obs'] - out['null_mean']
                     if np.isfinite(out['null_mean']) else np.nan)
    out['p_one_sided'] = (float((m >= out['obs']).mean()) if m.size else np.nan)
    out['obs_net30'] = out['obs'] - COST_PRIMARY / 10000.0
    out['null_mean_net30'] = (out['null_mean'] - COST_PRIMARY / 10000.0
                              if np.isfinite(out['null_mean']) else np.nan)
    out['null_lo_net30'] = (out['null_lo'] - COST_PRIMARY / 10000.0
                            if np.isfinite(out['null_lo']) else np.nan)
    out['null_hi_net30'] = (out['null_hi'] - COST_PRIMARY / 10000.0
                            if np.isfinite(out['null_hi']) else np.nan)
    return out


# =====================================================================
# overlap / independence audit (SPEC section 20)
# =====================================================================
def overlap_stats(si, ti, windows=PREREG_T['overlap_windows']):
    """Same-stock signal clustering.  Two-pointer per stock, so a stock with a
    long signal history does not make this quadratic."""
    si = np.asarray(si)
    ti = np.asarray(ti)
    out = {'n_events': int(si.size),
           'n_stocks': int(np.unique(si).size) if si.size else 0}
    if si.size == 0:
        return out
    order = np.lexsort((ti, si))
    s = si[order]
    t = ti[order].astype('int64')
    starts = np.flatnonzero(np.r_[True, s[1:] != s[:-1]])
    ends = np.r_[starts[1:], s.size]
    ws = sorted(int(w) for w in windows)
    pair_cnt = dict((w, 0) for w in ws)
    wmax = max(ws)
    nb = np.zeros(s.size, dtype='int64')
    total_pairs = 0
    for a, b in zip(starts, ends):
        tt = t[a:b]
        m = tt.size
        total_pairs += m * (m - 1) // 2
        if m < 2:
            continue
        for w in ws:
            p = 0
            c = 0
            for j in range(m):
                lim = tt[j] - w
                while tt[p] < lim:
                    p += 1
                c += (j - p)
            pair_cnt[w] += c
        # neighbours inside the widest window, per event
        p = 0
        for j in range(m):
            lim = tt[j] - wmax
            while tt[p] < lim:
                p += 1
            nb[a + j] += (j - p)
        q = m - 1
        for j in range(m - 1, -1, -1):
            lim = tt[j] + wmax
            while tt[q] > lim:
                q -= 1
            nb[a + j] += (q - j)
    out['total_same_stock_pairs'] = int(total_pairs)
    for w in ws:
        out['pairs_le%d' % w] = int(pair_cnt[w])
        out['rate_le%d' % w] = (float(pair_cnt[w]) / total_pairs
                                if total_pairs else np.nan)
    adj = int(pair_cnt.get(1, 0))
    out['adjacent_pairs'] = adj
    out['adjacent_rate'] = (float(adj) / total_pairs if total_pairs else np.nan)
    m = float(nb.mean())
    out['mean_same_stock_neighbours_%dd' % wmax] = m
    out['n_eff'] = float(si.size) / (1.0 + m)
    return out
