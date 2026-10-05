# -*- coding: utf-8 -*-
"""H-MCE-01 -- common layer.

Every rule in this module is transcribed from H_MCE_01_SPEC.md and was frozen
before any result was observed (see H_MCE_01_FREEZE.md).  Nothing here may be
edited after the first run without voiding the experiment.

Scope (SPEC section 1): ONLY the ten multi-candle price-volume events E01..E10.
No HVT / W7 / HVE / second-wave / theme / fundamentals / ML / RSI / MACD / KDJ.
No composite scoring model.  MA20 / MA60 / Volume are DISCRETE parts of the
event definitions, never a continuous score.

Future-information discipline (SPEC section 8): a signal mask may only use
columns t and earlier.  Confirmation masks (E0x_CONF) and diagnostic labels
(breached / REC*) may use t > T and are used ONLY as labels / anchor C.
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
for _p in (HVE_DIR, HRBP_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hve_common as H          # read-only dependency: panel + universe
import hrbp_common as R         # read-only dependency: tail / bootstrap

OUT = HERE                      # every artefact lives in the study directory
SPEC = os.path.join(HERE, 'H_MCE_01_SPEC.md')
FREEZE = os.path.join(HERE, 'H_MCE_01_FREEZE.md')
FS_DATA = H.FS_DATA

# SHA256 of the frozen SPEC -- must not change (SPEC section 21)
SPEC_SHA256 = '49E64CE9D7CCABCAADC81C0F03BF000F36E52834805B130120DC11499C7D4A2E'

# =====================================================================
# PREREG -- frozen before any H-MCE-01 result was observed
# =====================================================================
PREREG = {
    'hypothesis_id': 'H-MCE-01',
    'version': '1.0',
    'created': '2026-10-02',
    'title': 'A-share multi-candle price-volume event incremental-information test',

    # ---- sample / phases (SPEC sections 2.2, 9) ----------------------
    'study_start': '20190111',       # first session with any eligible cell
    'burn_in_sessions': 60,          # MA60 warm-up kept before study_start
    'is_start': '20190111',
    'is_end': '20231231',
    'oos_start': '20240102',
    'oos_end': '20260924',

    # ---- universe (SPEC section 2.3) ---------------------------------
    'universe_primary': "hve_common.eligibility(g) & (board != 'BSE')",
    'universe_full': "traded & ~st & (board != 'BSE')",

    # ---- event thresholds (SPEC section 4, task section 6) -----------
    'GAP2': 0.020,
    'GAP15': 0.015,
    'VR2': 2.0,
    'VR15': 1.5,
    'VR25': 2.5,
    'VR12': 1.2,
    'VR13': 1.3,
    'VRCAP': 1.0,
    'SHRINK': 0.7,
    'BODY15': 0.015,
    'RET3': 0.03,
    'RET7': 0.07,
    'PULL': 0.03,
    'PULL2': 0.05,
    'PULL3': 0.05,
    'SR': 0.5,
    'SHADOW_MULT': 1.5,
    'DDM': 0.05,
    'E05_CONF_LO': 6, 'E05_CONF_HI': 15,      # T+1..T+5 then +1..+10 -> 6..15
    'E03_CONF_LO': 1, 'E03_CONF_HI': 5,
    'E06_CONF_LO': 1, 'E06_CONF_HI': 5,
    'E07_CONF_LO': 1, 'E07_CONF_HI': 3,
    'E09_CONF_LO': 1, 'E09_CONF_HI': 3,

    # ---- forward returns (SPEC section 5) ----------------------------
    'horizons': (1, 3, 5, 10, 20),

    # ---- cost (SPEC section 6) ---------------------------------------
    'cost_bp': (0, 15, 30, 50),
    'primary_cost_bp': 30,

    # ---- statistics (SPEC sections 13, 14, 15) -----------------------
    'boot_B': 5000,
    'null_B': 2000,
    'null_rounds': 8,
    'null_min_resolution': 0.99,
    'seed': 20261002,
    'min_n': 30,
    'tail_levels': (0.01, 0.05, 0.10),
    'fdr_q': 0.10,

    # ---- parameter perturbation grid (SPEC section 10) ---------------
    'grid': {
        'gap': (0.015, 0.020, 0.025),
        'vr': (1.5, 2.0, 2.5),
        'gap15': (0.010, 0.015, 0.020),
        'vr15': (1.0, 1.5, 2.0),
        'ret7': (0.06, 0.07, 0.08),
        'ret3': (0.025, 0.030, 0.035),
        'shrink': (0.6, 0.7, 0.8),
        'vrcap': (0.8, 1.0, 1.2),
        'pull': (0.03, 0.05, 0.07),
        'vr25': (2.0, 2.5, 3.0),
        'shadow': (0.4, 0.5, 0.6),
        'vr12': (0.9, 1.2, 1.5),
        'vr13': (0.8, 1.3, 1.8),
        'hold': (3, 5, 7),
        'ddm': (0.03, 0.05, 0.07),
    },
    'param_stable_thr': 0.60,
    'param_fragile_thr': 0.40,

    # ---- regime (SPEC section 11, task section 13) -------------------
    'regime_index': '000300.SH',
    'regime_rule': 'BULL: Index>MA20>MA60 ; BEAR: Index<MA20<MA60 ; else NEUTRAL',

    # ---- momentum benchmark (SPEC section 16) ------------------------
    'mom_windows': (5, 10, 20),
    'mom_decile': 0.10,

    # ---- incremental model (SPEC section 17) -------------------------
    'incremental_n_sub': 400000,
    'incremental_horizons': (5, 10),

    'trading_authorization': 'NO',
}

SEED = PREREG['seed']
COSTS = PREREG['cost_bp']
COST_PRIMARY = PREREG['primary_cost_bp']
HORIZONS = PREREG['horizons']
B_BOOT = PREREG['boot_B']
B_NULL = PREREG['null_B']

MATKEYS = ('open', 'high', 'low', 'close', 'vol', 'amount', 'turnover',
           'pct_chg', 'traded', 'st', 'delist', 'limup', 'limdn', 'oneword')

# label columns: permitted to use t > T, never as a T-day signal (SPEC 8/21)
LABEL_COLUMNS = ('E02_CONF', 'E03_CONF', 'E04_CONF', 'E05_RETRACE',
                 'E05_CONF', 'E06_CONF', 'E06_REC50', 'E06_REC75',
                 'E06_RECHI', 'E07_CONF', 'E07_BREACHED', 'E08_CONF',
                 'E08_FILLED', 'E09_CONF', 'E10_CONF')

EVENT_NAMES = ('E01', 'E02', 'E03', 'E03_CONF', 'E04', 'E04_CONF', 'E05',
               'E05_RETRACE', 'E05_CONF', 'E06', 'E06_CONF', 'E06_REC50',
               'E06_REC75', 'E06_RECHI', 'E07', 'E07_CONF', 'E08',
               'E08_CONF', 'E08_FILLED', 'E09', 'E09_CONF', 'E10', 'E10_CONF')

# the ten primary events reported in the main table (SPEC section 4)
CORE_EVENTS = ('E01', 'E02', 'E03', 'E04', 'E05', 'E06', 'E07', 'E08',
               'E09', 'E10')


# =====================================================================
# logging / io
# =====================================================================
class Log(object):
    def __init__(self, name='mce_run'):
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
# rolling primitives -- time is axis 1
# =====================================================================
def _roll(M, w, how='mean', mp=None):
    d = pd.DataFrame(np.asarray(M, dtype='float32').T)
    roll = d.rolling(w, min_periods=(w if mp is None else mp))
    v = roll.max() if how == 'max' else roll.mean()
    return v.to_numpy().T.astype('float32')


def _prev(M):
    """Shift by one session: out[:, t] = M[:, t-1] (first column NaN)."""
    out = np.full(M.shape, np.nan, dtype='float32')
    out[:, 1:] = M[:, :-1]
    return out


def _div(a, b):
    a = np.asarray(a, dtype='float32')
    b = np.asarray(b, dtype='float32')
    with np.errstate(invalid='ignore', divide='ignore'):
        return np.where(np.isfinite(a) & np.isfinite(b) & (b != 0),
                        a / b, np.nan).astype('float32')


def _shift(M, d):
    """out[:, t] = M[:, t+d]; out-of-range slots are NaN / False."""
    M = np.asarray(M)
    S, N = M.shape
    if M.dtype == bool:
        out = np.zeros((S, N), dtype=bool)
    else:
        out = np.full((S, N), np.nan, dtype=M.dtype)
    if d == 0:
        return np.array(M, copy=True)
    if d > 0:
        out[:, :N - d] = M[:, d:]
    else:
        out[:, -d:] = M[:, :N + d]
    return out


def _window_bool(cond, lo, hi, mode='all'):
    """Fold a daily condition over the window [t+lo, t+hi]."""
    S, N = cond.shape
    acc = np.ones((S, N), dtype=bool) if mode == 'all' else np.zeros(
        (S, N), dtype=bool)
    for d in range(lo, hi + 1):
        v = _shift(cond, d)
        if mode == 'all':
            acc &= v
        else:
            acc |= v
    return acc


def fwd_all_ge(field, thr, lo, hi):
    """all_{d in [lo,hi]} field[t+d] >= thr[t]  (thr is an event-day matrix)."""
    S, N = field.shape
    acc = np.ones((S, N), dtype=bool)
    for d in range(lo, hi + 1):
        f = _shift(field, d)
        with np.errstate(invalid='ignore'):
            ok = np.isfinite(f) & np.isfinite(thr) & (f >= thr)
        acc &= ok
    return acc


def fwd_all_lt(field, thr, lo, hi):
    S, N = field.shape
    acc = np.ones((S, N), dtype=bool)
    for d in range(lo, hi + 1):
        f = _shift(field, d)
        with np.errstate(invalid='ignore'):
            ok = np.isfinite(f) & np.isfinite(thr) & (f < thr)
        acc &= ok
    return acc


def fwd_mean(field, lo, hi):
    S, N = field.shape
    s = np.zeros((S, N), dtype='float64')
    c = np.zeros((S, N), dtype='int16')
    for d in range(lo, hi + 1):
        f = _shift(field, d).astype('float64')
        m = np.isfinite(f)
        s += np.where(m, f, 0.0)
        c += m.astype('int16')
    with np.errstate(invalid='ignore'):
        return np.where(c > 0, s / np.maximum(c, 1), np.nan).astype('float32')


def fwd_min(field, lo, hi):
    S, N = field.shape
    acc = np.full((S, N), np.nan, dtype='float32')
    for d in range(lo, hi + 1):
        acc = np.fmin(acc, _shift(field, d))
    return acc


def fwd_max(field, lo, hi):
    S, N = field.shape
    acc = np.full((S, N), np.nan, dtype='float32')
    for d in range(lo, hi + 1):
        acc = np.fmax(acc, _shift(field, d))
    return acc


def first_hit_off(cond, lo, hi):
    """Smallest d in [lo,hi] with cond[t+d] True, else -1.  cond is fixed."""
    S, N = cond.shape
    best = np.full((S, N), -1, dtype='int32')
    for d in range(hi, lo - 1, -1):
        v = _shift(cond, d)
        best[v] = d
    return best


def first_hit_gt(field, thr, lo, hi):
    """Smallest d in [lo,hi] with field[t+d] > thr[t], else -1."""
    S, N = field.shape
    best = np.full((S, N), -1, dtype='int32')
    for d in range(hi, lo - 1, -1):
        f = _shift(field, d)
        with np.errstate(invalid='ignore'):
            ok = np.isfinite(f) & np.isfinite(thr) & (f > thr)
        best[ok] = d
    return best


def ret_matrix(cl, h):
    """close[t+h] / close[t] - 1 over the whole slice (NaN when undefined)."""
    S, N = cl.shape
    out = np.full((S, N), np.nan, dtype='float32')
    if 0 < h < N:
        a = cl[:, :N - h]
        b = cl[:, h:]
        with np.errstate(invalid='ignore'):
            out[:, :N - h] = np.where(
                (a > 0) & np.isfinite(a) & np.isfinite(b), b / a - 1.0, np.nan)
    return out


# =====================================================================
# state
# =====================================================================
class State(object):
    """Everything the experiment needs, restricted to the study window."""

    def __init__(self, g, lg=None, trunc=0, universe='U1'):
        self.trunc = int(trunc)
        self.universe = universe
        gg = truncate(g, trunc) if trunc else g
        N_full = gg['close'].shape[1]
        dates = gg['dates']
        i0 = int(np.searchsorted(dates, PREREG['study_start']))
        pad = int(PREREG['burn_in_sessions'])
        c0 = max(0, i0 - pad)
        self.c0, self.s0 = c0, i0 - c0
        sl = slice(c0, N_full)

        board = gg['board']
        if universe == 'U2':
            el = gg['traded'] & (~gg['st']) & (board != 'BSE')[:, None]
        else:
            el = H.eligibility(gg) & (board != 'BSE')[:, None]

        self.g = gg
        self.dates = dates
        self.N_full = N_full
        self.dates_w = dates[sl]
        self.codes = gg['codes']
        self.S = gg['close'].shape[0]
        self.N = N_full - c0
        self.sl = sl

        self.o = gg['open'][:, sl].astype('float32')
        self.hi = gg['high'][:, sl].astype('float32')
        self.lo = gg['low'][:, sl].astype('float32')
        self.cl = gg['close'][:, sl].astype('float32')
        self.traded = gg['traded'][:, sl]
        self.el = el[:, sl]
        self.vol = np.where(self.traded, gg['vol'][:, sl],
                            np.nan).astype('float32')
        amt = np.where(self.traded, gg['amount'][:, sl],
                       np.nan).astype('float32')

        # ---- moving averages / volumes (SPEC section 3) --------------
        self.ma20 = _roll(self.cl, 20)
        self.ma60 = _roll(self.cl, 60)
        mav5 = _prev(_roll(self.vol, 5))
        mav20 = _prev(_roll(self.vol, 20))
        mava20 = _prev(_roll(amt, 20))
        self.vr5 = _div(self.vol, mav5)
        self.vr20 = _div(self.vol, mav20)
        self.ar20 = _div(amt, mava20)
        self.rh20 = _prev(_roll(self.hi, 20, how='max'))
        del mav5, mav20, mava20, amt

        # ---- per-session descriptors (SPEC section 3) ----------------
        self.pc = _prev(self.cl)                     # prev_close := close[t-1]
        self.ret_1d = _div(self.cl, self.pc) - 1.0
        self.body = np.abs(self.cl - self.o).astype('float32')
        self.body_pct = _div(self.body, self.pc)
        self.upper = (self.hi - np.maximum(self.o, self.cl)).astype('float32')
        self.lower = (np.minimum(self.o, self.cl) - self.lo).astype('float32')
        self.gap = _div(self.o, self.pc) - 1.0
        self.rng = _div((self.hi - self.lo).astype('float32'), self.pc)
        hl = (self.hi - self.lo).astype('float32')
        with np.errstate(invalid='ignore'):
            self.cpos = np.where(hl > 0, (self.cl - self.lo) / hl,
                                 np.nan).astype('float32')

        # ---- eligibility x finite-feature gate -----------------------
        self.ef = (self.el & np.isfinite(self.ma20) & np.isfinite(self.ma60)
                   & np.isfinite(self.vr20))

        self.mom = {}
        for w in PREREG['mom_windows']:
            self.mom[w] = _div(self.cl, _shift(self.cl, w)) - 1.0

        self._build_events(lg)

        if lg:
            lg('state window %s..%s  cols=%d  burn-in=%d  universe=%s'
               % (self.dates_w[0], self.dates_w[-1], self.N, self.s0,
                  universe))
            lg('eligible density in window %.4f  eligible cells %d'
               % (float(self.el.mean()), int(self.el[:, self.s0:].sum())))

    # ------------------------------------------------------------------
    def _build_events(self, lg=None):
        P = PREREG
        o, hi, lo, cl, pc = self.o, self.hi, self.lo, self.cl, self.pc
        ef, vol, vr20 = self.ef, self.vol, self.vr20
        S, N = self.S, self.N
        E = {}

        # --- E01 巨量高开假阴线 --------------------------------------
        E01 = (ef & (self.gap >= P['GAP2']) & (cl < o) & (cl > pc)
               & (vr20 >= P['VR2']) & (cl > self.ma20))
        E['E01'] = {'T': E01, 'C': None, 'conf': False}

        # --- E02 次日阳包阴 ------------------------------------------
        c2 = ((cl > o) & (cl > _prev(hi)) & (o <= _prev(cl)))
        E['E02'] = {'T': E01 & _shift(c2, 1), 'C': None, 'conf': False}

        # --- E03 涨停/大阳 -> 高开阴 -> 再突破 -----------------------
        prev7 = _shift(self.ret_1d, 1) >= P['RET7']
        e03 = (ef & prev7 & (self.gap >= P['GAP15']) & (cl < o)
               & (vr20 >= P['VR15']) & (cl >= pc))
        c3 = first_hit_gt(cl, hi, P['E03_CONF_LO'], P['E03_CONF_HI'])
        E['E03'] = {'T': e03, 'C': None, 'conf': False}
        E['E03_CONF'] = {'T': e03, 'C': c3, 'conf': True}

        # --- E04 大阳 -> 缩量小阴 -> 再突破 --------------------------
        prev3 = _shift(self.ret_1d, 1) >= P['RET3']
        e04 = (ef & prev3 & (cl < o) & (self.body_pct <= P['BODY15'])
               & (_div(vol, _prev(vol)) <= P['SHRINK'])
               & (vr20 <= P['VRCAP']))
        c4 = first_hit_gt(cl, hi, P['E03_CONF_LO'], P['E03_CONF_HI'])
        E['E04'] = {'T': e04, 'C': None, 'conf': False}
        E['E04_CONF'] = {'T': e04, 'C': c4, 'conf': True}

        # --- E05 放量突破 -> 缩量回踩 -> 再突破 ----------------------
        e05 = ef & (cl > self.rh20) & (vr20 >= P['VR15'])
        pb_thr = self.rh20 * (1.0 - P['PULL'])
        pb = np.full((S, N), -1, dtype='int32')
        for d in range(5, 0, -1):
            with np.errstate(invalid='ignore'):
                ok = (_shift(vr20, d) < P['VRCAP']) & (_shift(lo, d) >= pb_thr)
            pb[ok] = d
        c5 = nc5 = np.full((S, N), -1, dtype='int32')
        for dd in range(10, 0, -1):
            idx = (np.arange(N)[None, :] + pb + dd)
            valid = (pb >= 0) & (idx <= N - 1)
            jj = np.clip(idx, 0, N - 1)
            gr = cl[np.arange(S)[:, None], jj]
            with np.errstate(invalid='ignore'):
                cond = np.isfinite(gr) & np.isfinite(hi) & (gr > hi)
            m = valid & cond
            c5[m] = (pb + dd)[m]
            nc5[m] = pb[m]
        E['E05'] = {'T': e05, 'C': None, 'conf': False}
        E['E05_RETRACE'] = {'T': e05, 'C': pb, 'conf': True}
        E['E05_CONF'] = {'T': e05, 'C': c5, 'conf': True}

        # --- E06 天量阴线 -> 缩量 -> 收复 50% ------------------------
        e06 = ef & (cl < o) & (vr20 >= P['VR25'])
        mid = ((hi + lo) / 2.0).astype('float32')
        thr50 = (lo + (hi - lo) * 0.50).astype('float32')
        thr75 = (lo + (hi - lo) * 0.75).astype('float32')
        E['E06'] = {'T': e06, 'C': None, 'conf': False}
        E['E06_CONF'] = {'T': e06,
                         'C': first_hit_gt(cl, mid, P['E06_CONF_LO'],
                                           P['E06_CONF_HI']), 'conf': True}
        E['E06_REC50'] = {'T': e06,
                          'C': first_hit_gt(cl, thr50, P['E06_CONF_LO'],
                                            P['E06_CONF_HI']), 'conf': True}
        E['E06_REC75'] = {'T': e06,
                          'C': first_hit_gt(cl, thr75, P['E06_CONF_LO'],
                                            P['E06_CONF_HI']), 'conf': True}
        E['E06_RECHI'] = {'T': e06,
                          'C': first_hit_gt(cl, hi, P['E06_CONF_LO'],
                                            P['E06_CONF_HI']), 'conf': True}

        # --- E07 长下影 -> 缩量 -> 放量阳 ----------------------------
        with np.errstate(invalid='ignore'):
            sr = np.where((hi - lo) > 0, self.lower / (hi - lo), np.nan)
        e07 = (ef & np.isfinite(sr) & (sr >= P['SR'])
               & (self.lower >= self.body * P['SHADOW_MULT'])
               & (_shift(vol, 1) < vol))
        c7d = (cl > o) & (vr20 >= P['VR12'])
        E['E07'] = {'T': e07, 'C': None, 'conf': False}
        E['E07_CONF'] = {'T': e07,
                         'C': first_hit_off(c7d, P['E07_CONF_LO'],
                                            P['E07_CONF_HI']), 'conf': True}
        # diagnostic label only (future information)
        self.e07_breached = (e07 & (fwd_min(lo, 1, 10) < lo))

        # --- E08 跳空 -> 缩量回踩不补缺 -> 再突破 --------------------
        hip = _prev(hi)
        e08t = ef & (self.gap >= P['GAP2']) & (lo > hip)
        hold_ok = fwd_all_ge(lo, hip, 1, 5) & (fwd_mean(vol, 1, 5) < vol)
        c8 = first_hit_gt(cl, hi, P['E05_CONF_LO'], P['E05_CONF_HI'])
        E['E08'] = {'T': e08t & hold_ok, 'C': None, 'conf': False}
        E['E08_CONF'] = {'T': e08t & hold_ok, 'C': c8, 'conf': True}
        E['E08_FILLED'] = {'T': e08t & ~fwd_all_ge(lo, hip, 1, 5),
                           'C': None, 'conf': False}

        # --- E09 大阳 -> 2~4 日缩量 -> 再放量阳 ----------------------
        e09t = ef & (self.ret_1d >= P['RET3']) & (vr20 >= P['VR15'])
        vthr = vol * P['SHRINK']
        lthr = cl * (1.0 - P['PULL2'])
        ok2 = fwd_all_lt(vol, vthr, 1, 2) & fwd_all_ge(lo, lthr, 1, 2)
        ok3 = ok2 & fwd_all_lt(vol, vthr, 3, 3) & fwd_all_ge(lo, lthr, 3, 3)
        ok4 = ok3 & fwd_all_lt(vol, vthr, 4, 4) & fwd_all_ge(lo, lthr, 4, 4)
        koff = np.where(ok2, 2, np.where(ok3, 3, np.where(ok4, 4, -1))
                        ).astype('int32')
        e09 = e09t & (koff > 0)
        c9d = (cl > o) & (vr20 >= P['VR13'])
        c9 = np.full((S, N), -1, dtype='int32')
        for dd in range(3, 0, -1):
            idx = (np.arange(N)[None, :] + koff + dd)
            valid = (koff > 0) & (idx <= N - 1)
            jj = np.clip(idx, 0, N - 1)
            gr = cl[np.arange(S)[:, None], jj]
            with np.errstate(invalid='ignore'):
                cond = (np.isfinite(gr) & np.isfinite(hi) & (gr > hi)
                        & c9d[np.arange(S)[:, None], jj])
            m = valid & cond
            c9[m] = (koff + dd)[m]
        E['E09'] = {'T': e09, 'C': None, 'conf': False}
        E['E09_CONF'] = {'T': e09, 'C': c9, 'conf': True}

        # --- E10 天量 -> 缩量横盘 -> 二次突破 ------------------------
        e10t = ef & (vr20 >= P['VR25'])
        dd_win_max = fwd_max(cl, 0, 5)
        dd_win_min = fwd_min(cl, 1, 5)
        with np.errstate(invalid='ignore'):
            ddv = (dd_win_max - dd_win_min) / dd_win_max
        flat = ((fwd_mean(vr20, 1, 5) <= P['VRCAP'])
                & np.isfinite(ddv) & (ddv <= P['DDM'])
                & fwd_all_ge(lo, cl * (1.0 - P['PULL3']), 1, 5))
        c10 = first_hit_gt(cl, hi, P['E05_CONF_LO'], P['E05_CONF_HI'])
        E['E10'] = {'T': e10t & flat, 'C': None, 'conf': False}
        E['E10_CONF'] = {'T': e10t & flat, 'C': c10, 'conf': True}

        self.E = E
        if lg:
            for nm in EVENT_NAMES:
                lg('  event %-12s n=%d' % (nm, int(
                    (E[nm]['T'] & self._win())[:, self.s0:].sum())))
            del nc5

    def _win(self):
        w = getattr(self, '_winmat', None)
        if w is None:
            w = np.zeros((self.S, self.N), dtype=bool)
            w[:, self.s0:] = True
            self._winmat = w
        return w

    # ------------------------------------------------------------------
    def samples(self, name):
        """-> dict(si, j, t_abs, aj, anchor_abs) for one event name.

        j / aj are SLICE indices (into the study-window matrices used here);
        t_abs / anchor_abs are absolute panel indices (for dates / legs).
        """
        e = self.E[name]
        T = e['T']
        si, j = np.nonzero(T & self._win())
        if e['conf']:
            coff = e['C'][si, j]
            keep = (coff >= 0)
            si, j, coff = si[keep], j[keep], coff[keep]
            aj = j + coff
        else:
            aj = j.copy()
        keep = aj <= self.N - 1
        si, j, aj = si[keep], j[keep], aj[keep]
        return {'si': si, 'j': j, 't_abs': j + self.c0,
                'aj': aj, 'anchor_abs': aj + self.c0}

    def months(self, t_abs):
        return np.array([self.dates[int(t)][:6] for t in np.asarray(t_abs)],
                        dtype=object)

    def years(self, t_abs):
        return np.array([int(self.dates[int(t)][:4]) for t in np.asarray(t_abs)],
                        dtype='int32')

    def phase(self, t_abs):
        d = np.array([self.dates[int(t)] for t in np.asarray(t_abs)],
                     dtype=object)
        out = np.full(d.size, 'PRE', dtype=object)
        out[(d >= PREREG['is_start']) & (d <= PREREG['is_end'])] = 'IS'
        out[(d >= PREREG['oos_start']) & (d <= PREREG['oos_end'])] = 'OOS'
        return out

    def is_window(self):
        d = np.array([self.dates[int(x)] for x in
                      range(self.c0, self.N_full)], dtype=object)
        return ((d >= PREREG['is_start']) & (d <= PREREG['is_end']))


# =====================================================================
# forward return legs (anchor close -> close[anchor+h])
# =====================================================================
def fwd(g, si, anchor, h, field='close'):
    """field[anchor+h] / close[anchor] - 1 (NaN outside the panel)."""
    N = g['close'].shape[1]
    si = np.asarray(si, dtype='int64')
    a = np.asarray(anchor, dtype='int64')
    j = a + int(h)
    ok = (a >= 0) & (a <= N - 1) & (j >= 0) & (j <= N - 1)
    ac = np.clip(a, 0, N - 1)
    jc = np.clip(j, 0, N - 1)
    p0 = g['close'][si, ac].astype('float64')
    p1 = g[field][si, jc].astype('float64')
    with np.errstate(invalid='ignore'):
        r = np.where((p0 > 0) & np.isfinite(p0) & np.isfinite(p1),
                     p1 / p0 - 1.0, np.nan)
    return np.where(ok, r, np.nan)


def path_stats(g, si, anchor, h):
    """Mean MAE / MFE / max-drawdown of the close path over anchor+1..anchor+h."""
    cl = g['close']
    N = cl.shape[1]
    si = np.asarray(si, dtype='int64')
    a = np.asarray(anchor, dtype='int64')
    base = cl[si, np.clip(a, 0, N - 1)].astype('float64')
    mae = np.full(a.size, np.nan)
    mfe = np.full(a.size, np.nan)
    mdd = np.full(a.size, np.nan)
    for d in range(1, h + 1):
        j = a + d
        ok = (base > 0) & np.isfinite(base) & (j <= N - 1)
        jc = np.clip(j, 0, N - 1)
        p = cl[si, jc].astype('float64')
        with np.errstate(invalid='ignore'):
            rel = np.where(ok & np.isfinite(p), p / base - 1.0, np.nan)
        mae = np.fmin(mae, rel)
        mfe = np.fmax(mfe, rel)
        if d == 1:
            cummax = np.fmax(np.zeros_like(rel), rel)
            mdd = np.fmax(mdd, cummax - rel)
        else:
            cummax = np.fmax(cummax, rel)
            mdd = np.fmax(mdd, cummax - rel)
    return (float(np.nanmean(mae)) if np.isfinite(mae).any() else np.nan,
            float(np.nanmean(mfe)) if np.isfinite(mfe).any() else np.nan,
            float(np.nanmean(mdd)) if np.isfinite(mdd).any() else np.nan)


# =====================================================================
# statistics
# =====================================================================
def _clean(r):
    x = np.asarray(r, dtype='float64')
    return x[np.isfinite(x)]


def metrics(r, costs=COSTS, primary=COST_PRIMARY):
    x = _clean(r)
    out = {'n': int(x.size)}
    if x.size == 0:
        for k in ('mean', 'median', 'win', 'std', 'tstat', 'max', 'min',
                  'p10', 'p25', 'p75', 'p90'):
            out[k] = np.nan
        for bp in costs:
            out['mean_net%d' % bp] = np.nan
            out['win_net%d' % bp] = np.nan
        out['net_primary'] = np.nan
        out['win_primary'] = np.nan
        return out
    out['mean'] = float(x.mean())
    out['median'] = float(np.median(x))
    out['win'] = float((x > 0).mean())
    out['std'] = float(x.std(ddof=1)) if x.size > 1 else np.nan
    out['tstat'] = (float(x.mean() / (x.std(ddof=1) / np.sqrt(x.size)))
                    if x.size > 1 and x.std(ddof=1) > 0 else np.nan)
    out['max'] = float(x.max())
    out['min'] = float(x.min())
    for q in (10, 25, 75, 90):
        out['p%d' % q] = float(np.percentile(x, q))
    for bp in costs:
        xn = x - bp / 10000.0
        out['mean_net%d' % bp] = float(xn.mean())
        out['win_net%d' % bp] = float((xn > 0).mean())
    xn = x - primary / 10000.0
    out['net_primary'] = float(xn.mean())
    out['win_primary'] = float((xn > 0).mean())
    return out


def boot_mean(r, months, B=B_BOOT, seed=SEED):
    """Monthly cluster bootstrap of the mean (SPEC section 14)."""
    x = np.asarray(r, dtype='float64')
    m = np.asarray(months)
    ok = np.isfinite(x)
    x, m = x[ok], m[ok]
    res = dict(mean=np.nan, lo=np.nan, hi=np.nan, p_le0=np.nan,
               n_month=0, n=int(x.size))
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
               n_month=int(k))
    return res


def diff_boot(ra, ma, rb, mb, B=B_BOOT, seed=SEED):
    """Monthly-cluster bootstrap of mean(a) - mean(b)."""
    ra = np.asarray(ra, 'float64')
    rb = np.asarray(rb, 'float64')
    ma, mb = np.asarray(ma), np.asarray(mb)
    oa, ob = np.isfinite(ra), np.isfinite(rb)
    ra, ma = ra[oa], ma[oa]
    rb, mb = rb[ob], mb[ob]
    out = dict(obs=np.nan, lo=np.nan, hi=np.nan, p_le0=np.nan,
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
    a_ = sa[draws].sum(1) / np.maximum(ca[draws].sum(1), 1e-9)
    b_ = sb[draws].sum(1) / np.maximum(cb[draws].sum(1), 1e-9)
    d = (a_ - b_)
    d = d[np.isfinite(d)]
    out.update(obs=float(ra.mean() - rb.mean()),
               lo=float(np.percentile(d, 2.5)) if d.size else np.nan,
               hi=float(np.percentile(d, 97.5)) if d.size else np.nan,
               p_le0=float((d <= 0).mean()) if d.size else np.nan,
               n_month=int(k))
    return out


def bh_fdr(pvals):
    """Benjamini-Hochberg adjusted p-values (SPEC section 15)."""
    p = np.asarray(pvals, dtype='float64')
    q = np.full(p.size, np.nan)
    ok = np.isfinite(p)
    idx = np.flatnonzero(ok)
    if idx.size == 0:
        return q
    pv = p[idx]
    order = np.argsort(pv)
    m = pv.size
    ranked = pv[order]
    adj = ranked * m / np.arange(1, m + 1)
    adj = np.minimum.accumulate(adj[::-1])[::-1]
    adj = np.clip(adj, 0.0, 1.0)
    qq = np.empty(m)
    qq[order] = adj
    q[idx] = qq
    return q


# =====================================================================
# null models (SPEC section 13)
# =====================================================================
def build_pools(cells, keys):
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
    if uq.size:
        ok &= (uq[pp] == ev_key)
    else:
        ok &= np.zeros(ev_key.size, bool)
    L = np.where(ok, lo[pp], 0)
    Hh = np.where(ok, hi[pp], 0)
    return L, Hh, ok


def null_family(rflat, flat, lo, hi, uq, ev_key, ev_self, obs, B=B_NULL,
                seed=SEED, rounds=PREREG['null_rounds'], chunk=64):
    """Vectorised matched-resampling null family.

    For every event cell one alternative cell is drawn from its matched pool
    (never the event's own cell).  Replicates are produced in chunks so the
    (B x E) index block never has to be materialised.
    """
    L, Hh, okkey = pool_lookup(uq, lo, hi, ev_key)
    E = int(ev_key.size)
    means = []
    res_sum = 0.0
    rng = np.random.default_rng(seed)
    nb = 0
    while nb < B:
        b = min(chunk, B - nb)
        cnt = np.broadcast_to(Hh - L, (b, E)).astype('int64')
        rnd = (rng.random((b, E)) * cnt).astype('int64')
        idx = np.clip(L[None, :] + np.minimum(rnd, np.maximum(cnt - 1, 0)),
                      0, max(flat.size - 1, 0))
        v = flat[idx] if flat.size else np.full((b, E), -1, dtype='int64')
        for _ in range(int(rounds)):
            bad = (v == ev_self[None, :])
            if not bad.any():
                break
            nbad = int(bad.sum())
            rnd2 = (rng.random(nbad) * cnt[bad]).astype('int64')
            v[bad] = flat[np.clip(L[None, :].repeat(b, 0)[bad] + rnd2,
                                  0, max(flat.size - 1, 0))]
        good = (v >= 0) & okkey[None, :]
        res_sum += float(good.sum())
        rr = np.where(good, rflat[np.clip(v, 0, rflat.size - 1)], np.nan)
        with np.errstate(invalid='ignore'):
            m = np.nanmean(rr, axis=1)
        means.append(m)
        nb += b
    means = np.concatenate(means) if means else np.array([])
    m = means[np.isfinite(means)]
    resolution = res_sum / float(B * max(E, 1))
    out = dict(obs=float(obs), B=int(B), resolution=float(resolution),
               key_coverage=float(okkey.mean()), n_events=E,
               null_mean=float(m.mean()) if m.size else np.nan,
               null_lo=float(np.percentile(m, 2.5)) if m.size else np.nan,
               null_hi=float(np.percentile(m, 97.5)) if m.size else np.nan,
               null_std=float(m.std(ddof=1)) if m.size > 1 else np.nan)
    out['excess'] = (out['obs'] - out['null_mean']
                     if np.isfinite(out['null_mean']) else np.nan)
    out['p_one_sided'] = (float((m >= out['obs']).mean()) if m.size else np.nan)
    return out


def bucket_ret(pc):
    """Trailing return bucket: <0 / 0-3% / 3-5% / 5-7% / >=7%."""
    out = np.full(pc.shape, -2, dtype='int8')
    v = pc
    out[(v >= 0) & (v < 0.03)] = 0
    out[(v >= 0.03) & (v < 0.05)] = 1
    out[(v >= 0.05) & (v < 0.07)] = 2
    out[v >= 0.07] = 3
    return out


def bucket_vr(vr):
    out = np.full(vr.shape, 4, dtype='int8')
    ok = np.isfinite(vr)
    out[ok & (vr < 1.2)] = 0
    out[ok & (vr >= 1.2) & (vr < 1.5)] = 1
    out[ok & (vr >= 1.5) & (vr < 2.0)] = 2
    out[ok & (vr >= 2.0)] = 3
    return out


def overlap_stats(si, ti, windows=(1, 5, 20)):
    """Same-stock signal clustering (two-pointer per stock)."""
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
    out['total_same_stock_pairs'] = int(total_pairs)
    for w in ws:
        out['pairs_le%d' % w] = int(pair_cnt[w])
        out['rate_le%d' % w] = (float(pair_cnt[w]) / total_pairs
                                if total_pairs else np.nan)
    return out


# =====================================================================
# market regime from CSI300 (SPEC section 11)
# =====================================================================
def regime_ma(g, lg=None):
    """(N,) 'BULL'/'NEUTRAL'/'BEAR' from Index vs MA20 vs MA60 (task section 13)."""
    p = os.path.join(FS_DATA, 'index_panel.parquet')
    idx = pd.read_parquet(p)
    idx['trade_date'] = idx['trade_date'].astype(str)
    idx = idx[idx['ts_code'].astype(str) == PREREG['regime_index']]
    idx = idx.sort_values('trade_date')
    kmap = {d: i for i, d in enumerate(g['dates'])}
    z = np.full(len(g['dates']), np.nan)
    k = idx['trade_date'].map(kmap).to_numpy()
    v = pd.to_numeric(idx['close'], errors='coerce').to_numpy()
    m = np.isfinite(k) & np.isfinite(v)
    z[k[m].astype(int)] = v[m]
    z = pd.Series(z).ffill().to_numpy()
    ma20 = pd.Series(z).rolling(20, min_periods=20).mean().to_numpy()
    ma60 = pd.Series(z).rolling(60, min_periods=60).mean().to_numpy()
    reg = np.where((z > ma20) & (ma20 > ma60), 'BULL',
                   np.where((z < ma20) & (ma20 < ma60), 'BEAR', 'NEUTRAL'))
    reg = np.where(np.isfinite(z) & np.isfinite(ma20) & np.isfinite(ma60),
                   reg, 'NA').astype(object)
    if lg:
        for nm in ('BULL', 'NEUTRAL', 'BEAR', 'NA'):
            lg('  regime %-8s %d sessions' % (nm, int((reg == nm).sum())))
    return reg.astype(str)


def index_ret(g):
    p = os.path.join(FS_DATA, 'index_panel.parquet')
    idx = pd.read_parquet(p)
    idx['trade_date'] = idx['trade_date'].astype(str)
    idx = idx[idx['ts_code'].astype(str) == PREREG['regime_index']]
    idx = idx.sort_values('trade_date')
    kmap = {d: i for i, d in enumerate(g['dates'])}
    k = idx['trade_date'].map(kmap).to_numpy()
    v = pd.to_numeric(idx['close'], errors='coerce').to_numpy()
    m = np.isfinite(k) & np.isfinite(v)
    z = np.full(len(g['dates']), np.nan)
    z[k[m].astype(int)] = v[m]
    z = pd.Series(z).ffill().to_numpy()
    r = np.full(len(z), np.nan)
    r[1:] = z[1:] / z[:-1] - 1.0
    return r


def load_size_mv(g):
    """(S,N) total_mv (万元) aligned to the panel, NaN where missing."""
    p = os.path.join(FS_DATA, 'basic_panel.parquet')
    b = pd.read_parquet(p, columns=['ts_code', 'trade_date', 'total_mv'])
    b['ts_code'] = b['ts_code'].astype(str)
    b['trade_date'] = b['trade_date'].astype(str)
    d2i, s2i = g['d2i'], g['s2i']
    b = b[b['trade_date'].isin(d2i) & b['ts_code'].isin(s2i)]
    M = np.full(g['close'].shape, np.nan, dtype='float32')
    M[b['ts_code'].map(s2i).to_numpy(),
      b['trade_date'].map(d2i).to_numpy().astype('int64')] = \
        pd.to_numeric(b['total_mv'], errors='coerce').to_numpy(dtype='float64')
    return M
