# -*- coding: utf-8 -*-
"""H-SLG-01 -- common layer.

Every rule in this module is transcribed from H_SLG_01_SPEC.md and was frozen
before any result was observed (see H_SLG_01_FREEZE.md).  Nothing here may be
edited after the first run without invalidating the experiment.

Chain under study (never compressed into one score, SPEC section 0):

    第一波主升 -> 高位二次整理 -> 整理完成 -> 二次突破 -> 后续收益

All structure quantities are CAUSAL (SPEC section 2): they are computed from a
BACKWARD window ending at t, so candidate_date = confirmation_date = event_date.
No pivot / swing high-low future-confirmation version is used anywhere.
"""
import os
import sys
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
for _p in (os.path.join(ROOT, 'research', 'hve'),
           os.path.join(ROOT, 'research', 'h_mce'),
           os.path.join(ROOT, 'research', 'h_earn_fwd')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hve_common as H          # read-only dependency: panel / universe / stats
import mce_common as MC         # read-only dependency: regime / index / market cap

OUT = HERE
SPEC = os.path.join(HERE, 'H_SLG_01_SPEC.md')
FREEZE = os.path.join(HERE, 'H_SLG_01_FREEZE.md')
RESULT_JSON = os.path.join(HERE, 'H_SLG_01_RESULTS.json')

# SHA256 of the frozen SPEC -- must not change (SPEC section 22 / FREEZE section 1)
SPEC_SHA256 = '387CD3B79191C9FB541DD74AA5DC93805FDEBA0F924FB1BEBADC290480DC9FB3'

# =====================================================================
# PREREG_S -- frozen before any H-SLG-01 result was observed
# =====================================================================
PREREG_S = {
    'hypothesis_id': 'H-SLG-01',
    'version': '1.0',
    'created': '2026-10-03',
    'title': 'Second leg: post-surge consolidation completion '
             '+ second-breakout incremental information',

    # ---- sample / phases (SPEC section 3) ----------------------------
    'study_start': '20190101',
    'burn_in_sessions': 250,
    'phase_IS': (2020, 2024),
    'phase_VALID': (2025, 2025),
    'phase_OOS': (2026, 2026),

    # ---- causal structure windows (SPEC section 5.1) -----------------
    'LEG_W': 40,
    'CONS_MIN': 5,
    'CONS_MAX': 40,
    'PEAK_LO': 5,
    'PEAK_HI': 45,

    # ---- first leg (SPEC section 5.3) --------------------------------
    'first_rise_pct': 0.25,
    'first_rise_days': 5,

    # ---- candidate gate (SPEC section 5.5) ---------------------------
    'retrace_min': 0.05,

    # ---- structural type thresholds (SPEC section 5.4) ---------------
    'type_A_lo': 0.98,
    'type_A_hi': 1.05,
    'type_D_retr': 0.08,

    # ---- READY bits R1..R6 (SPEC section 7.1) ------------------------
    'RB_MIN_DAYS': 2,
    'RB_AGE_MIN': 3,
    'NEW_LOW_TOL': 0.005,
    'VOL_DOWN_MAX': 1.00,
    'STRUCT_TOL': 0.03,
    'MA60_TOL': 0.05,
    'MA60_SLOPE_MIN': -0.01,
    'NEAR_TOL': 0.05,
    'RS_MIN': -0.05,

    # ---- FAILED bits F1..F5 (SPEC section 7.2) -----------------------
    'F2_LOOKBACK': 10,
    'F2_MIN_DAYS': 5,
    'F3_SLOPE': -0.02,
    'F4_VR_MEAN': 1.2,
    'F4_DOWN_VR': 1.5,
    'F4_SHARE': 0.4,
    'F5_RS_MEAN': -0.05,

    # ---- second breakout (SPEC section 8) ----------------------------
    'break_b': 0.00,
    'break_b_grid': (0.00, 0.01, 0.02, 0.03),
    'break_vol': 1.00,
    'break_vol_grid': (0.80, 1.00, 1.20, 1.50, 2.00),

    # ---- benchmark group B (SPEC section 9) --------------------------
    'groupB_vol_min': 1.00,

    # ---- returns (SPEC section 10) -----------------------------------
    'horizons': (1, 3, 5, 10, 20),
    'Hstar': 5,
    'label_brk_window': 10,
    'label_fail_drop': 0.03,
    'label_suc_rise': 0.05,

    # ---- costs (SPEC section 11) -------------------------------------
    'cost_bp': (0, 15, 30, 50),
    'primary_cost_bp': 30,

    # ---- parameter grid (SPEC section 12) ----------------------------
    'grid_first_rise_pct': (0.20, 0.25, 0.30),
    'grid_first_rise_days': (5, 10, 15),
    'grid_consol_max': (10, 20, 30, 40),
    'grid_retrace_min': (0.05, 0.10, 0.15, 0.20, 0.25, 0.30),
    'param_stable_thr': 0.60,
    'param_fragile_thr': 0.40,

    # ---- null (SPEC section 13) --------------------------------------
    'null_B': 2000,
    'null_rounds': 8,
    'null_min_resolution': 0.99,

    # ---- OOS / walk forward (SPEC section 14) ------------------------
    'wf_train_years': 3,
    'wf_test_years': 1,
    'wf_start_year': 2021,
    'wf_end_year': 2026,

    # ---- incremental models (SPEC section 15) ------------------------
    'N_SUB': 400000,
    'reg_h': 5,
    'reg_h2': 10,
    'reg_oos_frac': 0.5,

    # ---- subgroup breakpoints (SPEC section 16) ----------------------
    'leg_strength_cuts': (0.40, 0.60),
    'retr_cuts': (0.10, 0.20, 0.30),
    'vol_cuts': (0.8, 1.2),

    # ---- statistics --------------------------------------------------
    'seed': 20261101,
    'min_events': 30,
    'boot_B': 1000,
    'tail_levels': (0.01, 0.05, 0.10),

    # ---- universe ----------------------------------------------------
    'exclude_BSE': True,

    # ---- discipline --------------------------------------------------
    'trading_authorization': 'NO',
}

SEED = PREREG_S['seed']
COSTS = PREREG_S['cost_bp']
COST_PRIMARY = PREREG_S['primary_cost_bp']
HORIZONS = PREREG_S['horizons']
HSTAR = PREREG_S['Hstar']

MATKEYS = ('open', 'high', 'low', 'close', 'vol', 'amount', 'turnover',
           'pct_chg', 'traded', 'st', 'delist', 'limup', 'limdn', 'oneword')

# Columns that consume t > T information.  They are NEVER an input to any
# T-day signal (SPEC section 2.4).  future_column_scan asserts the disjointness.
LABEL_COLUMNS = tuple(
    ['fwd_ret_%d' % h for h in HORIZONS]
    + ['fwd_ret_mkt_%d' % h for h in HORIZONS]
    + ['fwd_ret_sec_%d' % h for h in HORIZONS]
    + ['suc_brk', 'fail_brk', 'label_brk_outcome', 'mfe_20', 'mae_20',
       'maxdd_20'])

SIGNAL_COLUMNS = ('CTX', 'READY', 'FAILED', 'CONSOLIDATING', 'R1', 'R2', 'R3',
                  'R4', 'R5', 'R6', 'F1', 'F2', 'F3', 'F4', 'F5', 'BREAKOUT',
                  'first_rise_pct', 'first_rise_days', 'retracement_pct',
                  'right_left_ratio', 'consol_days', 'bottom_spacing_days',
                  'struct_type')


# =====================================================================
# logging / io / small helpers
# =====================================================================
class Log(object):
    def __init__(self, name='slg_run'):
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
    g = H.build_grid(lg)
    if lg:
        lg('panel S=%d N=%d  %s..%s'
           % (g['close'].shape[0], g['close'].shape[1],
              g['dates'][0], g['dates'][-1]))
    return g


def truncate(g, K):
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
    d = pd.DataFrame(np.asarray(M, dtype='float64').T)
    roll = d.rolling(w, min_periods=(w if mp is None else mp))
    if how == 'mean':
        v = roll.mean()
    elif how == 'std':
        v = roll.std()
    elif how == 'max':
        v = roll.max()
    elif how == 'min':
        v = roll.min()
    elif how == 'sum':
        v = roll.sum()
    else:
        raise ValueError(how)
    return v.to_numpy().T.astype('float32')


def _prev(M):
    out = np.full(M.shape, np.nan, dtype='float32')
    out[:, 1:] = M[:, :-1]
    return out


def _slope(M, w, lag):
    out = np.full(M.shape, np.nan, dtype='float32')
    out[:, lag:] = (M[:, lag:] - M[:, :-lag]) / float(w)
    return out


def cum_M(M):
    """cumulative sum of finite values + cumulative finite count."""
    V = np.where(np.isfinite(M), np.asarray(M, dtype='float64'), 0.0)
    C = np.isfinite(M).astype('float64')
    return np.cumsum(V, axis=1), np.cumsum(C, axis=1)


def seg_sum(CS, si, a, b):
    """sum of CS over [a, b] inclusive, per flat cell (a/b are flat arrays)."""
    N = CS.shape[1]
    b_ = np.clip(np.asarray(b), 0, N - 1)
    hi = CS[si, b_]
    lo = np.zeros(si.size, dtype='float64')
    m = (np.asarray(a) - 1) >= 0
    if m.any():
        lo[m] = CS[si[m], np.clip(np.asarray(a)[m] - 1, 0, N - 1)]
    return hi - lo


# =====================================================================
# causal structure (SPEC section 5) -- backward windows only
# =====================================================================
def build_structure(hi, lo, lg=None):
    """Causal first-leg / consolidation structure.

    Returns a dict of (S,N) arrays.  Every value at column t uses only
    columns <= t:

      peak_off   offset (t - peak_day), in [PEAK_LO, PEAK_HI] or -1
      leg_off    offset (t - leg_start) or -1
      bounce_off offset (t - bounce_day) or -1
      lb_off     offset (t - left_bottom_day) or -1
      rb_off     offset (t - right_bottom_day) or -1
      first_rise_pct / first_rise_days
      retracement_pct / right_left_ratio / bottom_spacing_days
      neckline(=high[peak_day]) / bounce_high / left_bottom / right_bottom
      struct_type (1=A 2=B 3=C 4=D, 0 = unclassified)
      valid flags: v_peak v_leg v_bounce v_rb
    """
    P = PREREG_S
    S, N = hi.shape
    PEAK_LO, PEAK_HI = P['PEAK_LO'], P['PEAK_HI']
    CONS_MIN, CONS_MAX = P['CONS_MIN'], P['CONS_MAX']
    LEG_W = P['LEG_W']
    rows = np.arange(S)
    f32 = 'float32'
    NEG = -np.inf

    off_peak = np.arange(PEAK_HI, PEAK_LO - 1, -1)          # 45..5 earliest-first
    off_leg = np.arange(LEG_W, -1, -1)                     # 40..0 earliest-first
    off_b = np.arange(CONS_MAX - 1, -1, -1)                # 39..0 earliest-first
    n_b = off_b.size

    peak_off = np.full((S, N), -1, dtype='int16')
    leg_off = np.full((S, N), -1, dtype='int16')
    first_rise_days = np.full((S, N), -1, dtype='int16')
    bounce_off = np.full((S, N), -1, dtype='int8')
    lb_off = np.full((S, N), -1, dtype='int8')
    rb_off = np.full((S, N), -1, dtype='int8')
    first_rise_pct = np.full((S, N), np.nan, dtype=f32)
    retracement = np.full((S, N), np.nan, dtype=f32)
    rl_ratio = np.full((S, N), np.nan, dtype=f32)
    neckline = np.full((S, N), np.nan, dtype=f32)
    bounce_high = np.full((S, N), np.nan, dtype=f32)
    lb_px = np.full((S, N), np.nan, dtype=f32)
    rb_px = np.full((S, N), np.nan, dtype=f32)
    spacing = np.full((S, N), -1, dtype='int16')

    t0 = time.time()
    for t in range(PEAK_HI, N):
        # ---- peak: argmax high over [t-45, t-5], ties -> earliest -------
        cols = t - off_peak                                # ascending in time
        HW = np.where(np.isfinite(hi[:, cols]), hi[:, cols], NEG)
        pi = np.argmax(HW, axis=1)
        po = off_peak[pi].astype('int16')
        ok_peak = (po >= max(PEAK_LO, CONS_MIN)) & (po <= CONS_MAX)
        peak_off[:, t] = np.where(ok_peak, po, -1).astype('int16')

        pd_ = t - po.astype('int32')                       # peak_day

        # ---- leg start: argmin low over [peak_day-40, peak_day] ---------
        cols_l = pd_[:, None] - off_leg[None, :]
        ok_l = cols_l >= 0
        LW = np.where(ok_l, lo[rows[:, None], np.clip(cols_l, 0, N - 1)], np.inf)
        li = np.argmin(LW, axis=1)
        lo_ = off_leg[li].astype('int16')                  # first_rise_days
        ls_d = pd_ - lo_.astype('int32')                   # leg_start day
        ok_leg = ok_l.any(axis=1) & (lo_ >= P['first_rise_days'])

        lp = lo[rows, np.clip(ls_d, 0, N - 1)]
        hp = hi[rows, np.clip(pd_, 0, N - 1)]
        with np.errstate(invalid='ignore'):
            frp = np.where((lp > 0) & np.isfinite(lp) & np.isfinite(hp),
                           hp / lp - 1.0, np.nan)
        leg_off[:, t] = np.where(ok_leg, po + lo_, -1).astype('int16')
        first_rise_days[:, t] = np.where(ok_leg, lo_, -1).astype('int16')
        first_rise_pct[:, t] = frp.astype(f32)
        neckline[:, t] = hp.astype(f32)

        # ---- bounce: argmax high over [peak_day+1, t], ties -> earliest -
        cb = t - off_b                                     # ascending in time
        cb2 = np.broadcast_to(cb, (S, n_b))
        ok_b = cb2 >= (pd_[:, None] + 1)
        HB = np.where(ok_b, hi[rows[:, None], np.clip(cb2, 0, N - 1)], NEG)
        bi = np.argmax(HB, axis=1)
        bo = off_b[bi].astype('int8')
        bd = t - bo.astype('int32')
        ok_bounce = ok_b.any(axis=1)
        bounce_off[:, t] = bo
        bh = hi[rows, np.clip(bd, 0, N - 1)]
        bounce_high[:, t] = bh.astype(f32)

        # ---- left bottom: min low over [peak_day+1, bounce_day] ---------
        cl = bd[:, None] - off_b[None, :]
        ok_lb = cl >= (pd_[:, None] + 1)
        LB = np.where(ok_lb, lo[rows[:, None], np.clip(cl, 0, N - 1)], np.inf)
        lbi = np.argmin(LB, axis=1)
        lbd = bd - off_b[lbi].astype('int32')
        lbv = lo[rows, np.clip(lbd, 0, N - 1)]
        ok_lb2 = ok_lb.any(axis=1)
        lb_off[:, t] = np.where(ok_lb2, t - lbd, -1).astype('int8')
        lb_px[:, t] = lbv.astype(f32)

        # ---- right bottom: min low over [bounce_day+1, t] ---------------
        cr = t - off_b
        cr2 = np.broadcast_to(cr, (S, n_b))
        ok_rb = cr2 >= (bd[:, None] + 1)
        RB = np.where(ok_rb, lo[rows[:, None], np.clip(cr2, 0, N - 1)], np.inf)
        rbi = np.argmin(RB, axis=1)
        rbd = t - off_b[rbi].astype('int32')
        rbv = lo[rows, np.clip(rbd, 0, N - 1)]
        ok_rb2 = ok_rb.any(axis=1) & ((t - bd) >= P['RB_MIN_DAYS'])
        rb_off[:, t] = np.where(ok_rb2, t - rbd, -1).astype('int8')
        rb_px[:, t] = rbv.astype(f32)

        # ---- derived -----------------------------------------------------
        with np.errstate(invalid='ignore'):
            retr = np.where(ok_rb2 & (hp > 0), (hp - rbv) / hp, np.nan)
            ratio = np.where(ok_rb2 & (lbv > 0), rbv / lbv, np.nan)
        retracement[:, t] = retr.astype(f32)
        rl_ratio[:, t] = ratio.astype(f32)
        spacing[:, t] = np.where(ok_rb2 & ok_lb2, rbd - lbd, -1).astype('int16')
        if (t % 250) == 0 and lg:
            lg('  structure t=%d/%d (%.1fs)' % (t, N, time.time() - t0))

    v_peak = peak_off >= 0
    v_leg = leg_off >= 0
    v_bounce = bounce_off >= 0
    v_rb = rb_off >= 0

    # ---- structural type (SPEC 5.4): D takes precedence, then A/B/C -----
    code = np.zeros((S, N), dtype='int8')
    with np.errstate(invalid='ignore'):
        deep = v_rb & np.isfinite(retracement) & (retracement < P['type_D_retr'])
        Aw = (v_rb & np.isfinite(rl_ratio) & (rl_ratio >= P['type_A_lo'])
              & (rl_ratio <= P['type_A_hi']))
        Bw = (v_rb & np.isfinite(rl_ratio) & (rl_ratio > P['type_A_hi']))
        Cw = (v_rb & np.isfinite(rl_ratio) & (rl_ratio < P['type_A_lo']))
    code[Cw] = 3
    code[Bw] = 2
    code[Aw] = 1
    code[deep] = 4

    if lg:
        lg('structure built (%.1fs)  v_peak=%.4f v_leg=%.4f v_rb=%.4f'
           % (time.time() - t0, float(v_peak.mean()), float(v_leg.mean()),
              float(v_rb.mean())))
    return {
        'peak_off': peak_off, 'leg_off': leg_off, 'bounce_off': bounce_off,
        'lb_off': lb_off, 'rb_off': rb_off,
        'first_rise_pct': first_rise_pct,
        'first_rise_days': first_rise_days,
        'retracement_pct': retracement,
        'right_left_ratio': rl_ratio,
        'bottom_spacing_days': spacing,
        'neckline': neckline, 'bounce_high': bounce_high,
        'left_bottom': lb_px, 'right_bottom': rb_px,
        'struct_type': code,
        'v_peak': v_peak, 'v_leg': v_leg, 'v_bounce': v_bounce, 'v_rb': v_rb,
    }


# =====================================================================
# industry (PIT SW level-1) and sector/market strength
# =====================================================================
def industry_matrix(g, lg=None):
    """(S,N) int16 PIT SW level-1 id, -1 = unknown."""
    im = H.load_industry_map()
    l1s = sorted(str(v) for v in im['l1'].dropna().unique())
    l1id = {v: i for i, v in enumerate(l1s)}
    dates = g['dates']
    s2i = g['s2i']
    S, N = g['close'].shape
    M = np.full((S, N), -1, dtype='int16')
    for ts_code, grp in im.groupby('ts_code'):
        s = s2i.get(str(ts_code))
        if s is None:
            continue
        for r in grp.itertuples(index=False):
            j = l1id.get(str(r.l1))
            if j is None:
                continue
            a = int(np.searchsorted(dates, str(r.in_date), side='left'))
            b = int(np.searchsorted(dates, str(r.out_date), side='right')) - 1
            if b < a:
                continue
            b = min(b, N - 1)
            M[s, a:b + 1] = j
    if lg:
        cov = float((M >= 0).mean())
        lg('industry matrix: %d L1, coverage %.4f' % (len(l1s), cov))
    return M, l1s


def sector_market_strength(g, el, ind, lg=None):
    """Per-day equal-weight sector returns + per-cell relative strength.

    Returns dict with
      sec_ret_d   (S,N) sector EW daily return at the cell's own sector
      rs_sector   (S,N) r1 - sec_ret_d            (daily, for F5 / R6 inputs)
      rs_sector_20(S,N) r20_stock - r20_sector_ew
      rs_market_20(S,N) r20_stock - r20_csi300
      sec_ret5/20 (N,)  market-wide sector mean
      mkt_ret     (N,)  CSI300 daily return
    """
    P = PREREG_S
    cl = g['close']
    S, N = cl.shape
    r1 = np.full((S, N), np.nan, dtype='float32')
    r1[:, 1:] = np.where((cl[:, :-1] > 0) & (cl[:, 1:] > 0),
                         cl[:, 1:] / cl[:, :-1] - 1.0, np.nan)
    pre20 = np.full((S, N), np.nan, dtype='float32')
    pre20[:, 20:] = cl[:, :-20]
    with np.errstate(invalid='ignore'):
        r20 = np.where((pre20 > 0), cl / pre20 - 1.0, np.nan).astype('float32')

    n_l1 = int(ind.max()) + 1 if ind.size and ind.max() >= 0 else 0
    sec_ret_d = np.full((S, N), np.nan, dtype='float32')
    sec_ret20_d = np.full((S, N), np.nan, dtype='float32')
    sec_day_mean = np.full(N, np.nan)
    for t in range(N):
        m = el[:, t] & (ind[:, t] >= 0) & np.isfinite(r1[:, t])
        if not m.any():
            continue
        j = ind[m, t].astype('int64')
        v = r1[m, t].astype('float64')
        ssum = np.bincount(j, weights=v, minlength=n_l1)
        cnt = np.bincount(j, minlength=n_l1).astype('float64')
        mean = np.where(cnt > 0, ssum / np.maximum(cnt, 1e-9), np.nan)
        sec_ret_d[m, t] = mean[j].astype('float32')
        sec_day_mean[t] = float(np.nanmean(v))
        m2 = m & np.isfinite(r20[:, t])
        if m2.any():
            j2 = ind[m2, t].astype('int64')
            v2 = r20[m2, t].astype('float64')
            s2 = np.bincount(j2, weights=v2, minlength=n_l1)
            c2 = np.bincount(j2, minlength=n_l1).astype('float64')
            m2v = np.where(c2 > 0, s2 / np.maximum(c2, 1e-9), np.nan)
            sec_ret20_d[m2, t] = m2v[j2].astype('float32')

    mkt = MC.index_ret(g)                                   # CSI300 daily
    mkt = np.asarray(mkt, dtype='float64')
    pre20m = np.full(N, np.nan)
    pre20m[20:] = mkt[:-20]
    r20m = np.full(N, np.nan)
    with np.errstate(invalid='ignore'):
        base = 1.0 + np.nan_to_num(pre20m, nan=np.nan)
    # 20d index return = prod(1+r)-1 over [t-19, t]
    lr = np.log1p(np.nan_to_num(mkt, nan=0.0))
    cslr = np.cumsum(lr)
    r20m[20:] = np.exp(cslr[20:] - cslr[:-20]) - 1.0
    r20m[~np.isfinite(mkt)] = np.nan

    with np.errstate(invalid='ignore'):
        rs_sector = (r1 - sec_ret_d).astype('float32')
        rs_sector_20 = (r20 - sec_ret20_d).astype('float32')
        rs_market_20 = (r20 - r20m[None, :]).astype('float32')

    sec5 = np.full(N, np.nan)
    sec20 = np.full(N, np.nan)
    for t in range(N):
        m = el[:, t] & np.isfinite(r1[:, t])
        if m.any():
            sec5[t] = float(np.nanmean(r1[m, t]))
    # 20d sector-wide: mean of individual 20d returns
    for t in range(N):
        m = el[:, t] & np.isfinite(r20[:, t])
        if m.any():
            sec20[t] = float(np.nanmean(r20[m, t]))
    del base, lr, cslr
    if lg:
        lg('RS built: rs_sector finite %.4f  rs_market_20 finite %.4f'
           % (float(np.isfinite(rs_sector).mean()),
              float(np.isfinite(rs_market_20).mean())))
    return {'r1': r1, 'r20': r20, 'sec_ret_d': sec_ret_d,
            'sec_ret20_d': sec_ret20_d, 'rs_sector': rs_sector,
            'rs_sector_20': rs_sector_20, 'rs_market_20': rs_market_20,
            'mkt_ret': mkt.astype('float32'), 'r20m': r20m.astype('float32'),
            'sec5': sec5.astype('float32'), 'sec20': sec20.astype('float32')}


# =====================================================================
# State
# =====================================================================
class State(object):
    """Panel slice + every causal mask the SPEC needs."""

    def __init__(self, g, lg=None, trunc=0):
        P = PREREG_S
        self.trunc = int(trunc)
        gg = truncate(g, trunc) if trunc else g
        N_full = gg['close'].shape[1]
        dates = gg['dates']
        i0 = int(np.searchsorted(dates, P['study_start']))
        pad = int(P['burn_in_sessions'])
        c0 = max(0, i0 - pad)
        self.c0, self.s0 = c0, i0 - c0
        sl = slice(c0, N_full)

        board = gg['board']
        el_full = H.eligibility(gg)
        if P['exclude_BSE']:
            el_full = el_full & (board != 'BSE')[:, None]
        el2_full = gg['traded'] & ~gg['st']
        if P['exclude_BSE']:
            el2_full = el2_full & (board != 'BSE')[:, None]

        self.g = gg
        self.dates = dates
        self.dates_w = dates[sl]
        self.codes = gg['codes']
        self.board = board
        self.S, self.N = gg['close'].shape[0], N_full - c0
        self.sl = sl

        self.o = gg['open'][:, sl]
        self.hi = gg['high'][:, sl]
        self.lo = gg['low'][:, sl]
        self.cl = gg['close'][:, sl]
        self.pc = gg['pct_chg'][:, sl]
        self.vol = np.where(gg['traded'][:, sl], gg['vol'][:, sl],
                            np.nan).astype('float32')
        self.el = el_full[:, sl]
        self.el2 = el2_full[:, sl]

        # ---- MAs --------------------------------------------------------
        self.ma20 = _roll(self.cl, 20, mp=20)
        self.ma60 = _roll(self.cl, 60, mp=60)
        self.ma20_slope20 = _slope(self.ma20, 20, 20)
        self.ma60_slope20 = _slope(self.ma60, 20, 20)

        # ---- volatility -------------------------------------------------
        r1 = np.full(self.cl.shape, np.nan, dtype='float32')
        r1[:, 1:] = np.where((self.cl[:, :-1] > 0) & (self.cl[:, 1:] > 0),
                             self.cl[:, 1:] / self.cl[:, :-1] - 1.0, np.nan)
        self.r1 = r1
        self.std5 = _roll(r1, 5, how='std', mp=3)
        self.std20 = _roll(r1, 20, how='std', mp=10)
        tr = np.maximum(self.hi - self.lo,
                        np.maximum(np.abs(self.hi - _prev(self.cl)),
                                   np.abs(self.lo - _prev(self.cl))))
        self.atr5 = _roll(tr, 5, mp=3)
        with np.errstate(invalid='ignore'):
            self.atr_ratio = np.where(self.cl > 0, self.atr5 / self.cl,
                                      np.nan).astype('float32')
        del tr

        # ---- volume -----------------------------------------------------
        vm20 = _roll(self.vol, 20, mp=10)
        self.vol_ma20_prev = _prev(vm20)
        with np.errstate(invalid='ignore'):
            self.vr20 = np.where(self.vol_ma20_prev > 0,
                                 self.vol / self.vol_ma20_prev, np.nan
                                 ).astype('float32')
        self.hi20_prev = _roll(self.hi, 20, how='max', mp=20)
        self.hi20_prev = np.where(
            np.isfinite(self.hi20_prev), self.hi20_prev, np.nan)
        # shift by 1 so it excludes today
        self.hi20_prev = np.asarray(
            pd.DataFrame(self.hi.astype('float64').T).shift(1)
            .rolling(20, min_periods=20).max().to_numpy().T, dtype='float32')

        # ---- industry / RS ---------------------------------------------
        ind_full, l1s = industry_matrix(gg, lg)
        self.ind = np.ascontiguousarray(ind_full[:, sl])
        self.l1s = l1s
        sm = sector_market_strength(gg, el_full, ind_full, lg)
        for k, v in sm.items():
            if v.ndim == 2:
                setattr(self, k, np.ascontiguousarray(v[:, sl]))
            else:
                setattr(self, k, v[c0:])
        self.rs_sector = getattr(self, 'rs_sector')

        # ---- causal structure ------------------------------------------
        st = build_structure(self.hi, self.lo, lg)
        for k, v in st.items():
            setattr(self, 's_' + k, v)

        # ---- cumsums for window statistics ------------------------------
        self.CS_vol, self.CC_vol = cum_M(self.vol)
        self.CS_rs, self.CC_rs = cum_M(self.rs_sector)
        self.CS_r1, self.CC_r1 = cum_M(self.r1)
        self.CS_r1sq = np.cumsum(
            np.where(np.isfinite(self.r1), self.r1.astype('float64') ** 2, 0.0),
            axis=1)
        self.CS_vr, self.CC_vr = cum_M(self.vr20)
        self.b60 = (self.cl < self.ma60 * (1.0 - P['MA60_TOL']))
        self.CS_b60 = np.cumsum(self.b60.astype('int32'), axis=1)

        # ---- context mask CTX (candidate) ------------------------------
        frp = self.s_first_rise_pct
        with np.errstate(invalid='ignore'):
            self.CTX = (self.el & self.s_v_peak & self.s_v_leg
                        & (frp >= P['first_rise_pct'])
                        & (self.s_first_rise_days >= P['first_rise_days'])
                        & self.s_v_bounce & np.isfinite(self.s_retracement_pct)
                        & (self.s_retracement_pct >= P['retrace_min']))
        self.CTX = np.ascontiguousarray(self.CTX)

        # ---- event flat indices ----------------------------------------
        self._cand = None

        if lg:
            lg('state window %s..%s cols=%d burn-in=%d'
               % (self.dates_w[0], self.dates_w[-1], self.N, self.s0))
            lg('eligible %.4f  CTX cells %.6f  BSE excluded=%s'
               % (float(self.el.mean()), float(self.CTX.mean()),
                  P['exclude_BSE']))

    # ------------------------------------------------------------------
    def cand_idx(self):
        """Flat indices (s, j) of candidate CTX cells inside the study window."""
        if self._cand is None:
            m = self.CTX.copy()
            m[:, :self.s0] = False
            m[:, self.N - 1:] = False
            s, j = np.nonzero(m)
            self._cand = (s.astype('int64'), j.astype('int64'))
        return self._cand

    def win_days(self, off, j):
        """Absolute column of t - off for flat cells."""
        return np.asarray(j) - np.asarray(off)

    def years(self, j):
        return np.array([int(self.dates_w[int(x)][:4]) for x in np.asarray(j)],
                        dtype='int32')

    def months(self, j):
        return np.array([self.dates_w[int(x)][:6] for x in np.asarray(j)],
                        dtype=object)


# =====================================================================
# completion variables (SPEC section 6) -- computed on flat cell sets
# =====================================================================
def completion_vars(st, s, j):
    """All section-6 completion variables + R1..R6 / F1..F5 for flat cells."""
    P = PREREG_S
    st_ = st
    hi, lo, cl, vol = st_.hi, st_.lo, st_.cl, st_.vol
    ma20, ma60 = st_.ma20, st_.ma60

    po = st_.s_peak_off[s, j].astype('int64')
    bo = st_.s_bounce_off[s, j].astype('int64')
    lbo = st_.s_lb_off[s, j].astype('int64')
    rbo = st_.s_rb_off[s, j].astype('int64')
    leg = st_.s_leg_off[s, j].astype('int64')
    peak_day = j - po
    bounce_day = j - bo
    lb_day = j - lbo
    rb_day = j - rbo
    leg_start = j - leg

    E = s.size
    out = {}

    # ---- 6.1 price stability ----------------------------------------
    out['atr_ratio'] = st_.atr_ratio[s, j].astype('float64')
    out['std5'] = st_.std5[s, j].astype('float64')
    out['std20'] = st_.std20[s, j].astype('float64')
    # leg-window return std (first leg)
    n_leg = seg_sum(st_.CC_r1, s, leg_start, peak_day)
    s_leg = seg_sum(st_.CS_r1, s, leg_start, peak_day)
    s2_leg = seg_sum(st_.CS_r1sq, s, leg_start, peak_day)
    with np.errstate(invalid='ignore'):
        mu = np.where(n_leg > 0, s_leg / n_leg, np.nan)
        var = np.where(n_leg > 1, s2_leg / n_leg - mu ** 2, np.nan)
    leg_std = np.sqrt(np.maximum(var, 0.0))
    with np.errstate(invalid='ignore'):
        vr5_leg = np.where(leg_std > 0, out['std5'] / leg_std, np.nan)
    out['vol_ratio_5_leg'] = vr5_leg

    # ---- 6.2 volume contraction -------------------------------------
    v_leg_mean = _seg_mean(st_.CS_vol, st_.CC_vol, s, leg_start, peak_day)
    v_rb = vol[s, rb_day].astype('float64')
    v_ma20_prev = st_.vol_ma20_prev[s, rb_day].astype('float64')
    v_consol_mean = _seg_mean(st_.CS_vol, st_.CC_vol, s, peak_day + 1, j)
    with np.errstate(invalid='ignore'):
        out['rb_vol_over_leg'] = np.where(v_leg_mean > 0, v_rb / v_leg_mean,
                                          np.nan)
        out['rb_vol_over_ma20'] = np.where(v_ma20_prev > 0, v_rb / v_ma20_prev,
                                           np.nan)
        out['consol_vol_over_leg'] = np.where(v_leg_mean > 0,
                                              v_consol_mean / v_leg_mean, np.nan)

    # ---- 6.3 MA20 structure -----------------------------------------
    c = cl[s, j].astype('float64')
    m20 = ma20[s, j].astype('float64')
    m60 = ma60[s, j].astype('float64')
    with np.errstate(invalid='ignore'):
        out['close_vs_ma20'] = np.where(m20 > 0, c / m20 - 1.0, np.nan)
        out['close_vs_ma60'] = np.where(m60 > 0, c / m60 - 1.0, np.nan)
    out['ma20_slope20'] = st_.ma20_slope20[s, j].astype('float64')
    out['ma60_slope20'] = st_.ma60_slope20[s, j].astype('float64')
    # days above MA20 vs days below inside the consolidation window
    above = st_.cl > st_.ma20
    n_w = (j - peak_day).astype('int64')
    n_above = _seg_count(above, s, peak_day + 1, j)
    out['days_above_ma20'] = n_above.astype('float64')
    with np.errstate(invalid='ignore'):
        frac_below = np.where(n_w > 0, (n_w - n_above) / n_w, np.nan)
    out['frac_below_ma20'] = frac_below
    cat = np.full(E, -1, dtype='int8')       # 0 always-above 1 brief-below-recovered
    cat[(frac_below == 0)] = 0               # 2 sustained-below 3 ma20-turning
    now_above = np.isfinite(out['close_vs_ma20']) & (out['close_vs_ma20'] >= 0)
    cat[(frac_below > 0) & now_above] = 1
    cat[(frac_below > 0.5) & (~now_above)] = 2
    turning = np.isfinite(out['ma20_slope20']) & (out['ma20_slope20'] < 0)
    cat[turning & (cat != 2)] = 3
    out['ma20_category'] = cat

    # ---- 6.4 MA60 structure -----------------------------------------
    s60 = out['ma60_slope20']
    cat60 = np.full(E, -1, dtype='int8')     # 1 up / 0 flat / -1 down
    cat60[np.isfinite(s60) & (s60 > 1e-6)] = 1
    cat60[np.isfinite(s60) & (np.abs(s60) <= 1e-6)] = 0
    cat60[np.isfinite(s60) & (s60 < -1e-6)] = -1
    out['ma60_category'] = cat60

    # ---- 6.5 right-bottom structure ---------------------------------
    hp = hi[s, peak_day].astype('float64')
    rb_px = st_.s_right_bottom[s, j].astype('float64')
    lb_px = st_.s_left_bottom[s, j].astype('float64')
    with np.errstate(invalid='ignore'):
        out['rb_drawdown_from_peak'] = np.where(hp > 0, (hp - rb_px) / hp,
                                                np.nan)
        out['rb_dist_ma20'] = np.where(m20 > 0, rb_px / m20 - 1.0, np.nan)
        out['rb_dist_ma60'] = np.where(m60 > 0, rb_px / m60 - 1.0, np.nan)
        out['rb_dist_left_bottom'] = np.where(lb_px > 0, rb_px / lb_px - 1.0,
                                              np.nan)

    # ---- READY bits --------------------------------------------------
    rb_age = (j - rb_day).astype('int64')
    right_len = (j - bounce_day).astype('int64')
    R1 = right_len >= P['RB_MIN_DAYS']
    after_min = _seg_min(lo, s, rb_day + 1, j)
    no_new_low = ~np.isfinite(after_min) | (after_min >= rb_px * (1.0 - P['NEW_LOW_TOL']))
    R2 = (rb_age >= P['RB_AGE_MIN']) & no_new_low
    R3 = np.isfinite(vr5_leg) & (vr5_leg <= P['VOL_DOWN_MAX'])
    all_min = _seg_min(lo, s, peak_day + 1, j)
    s_ok = (~np.isfinite(all_min)) | (all_min >= lb_px * (1.0 - P['STRUCT_TOL']))
    ma60_ok = (~np.isfinite(m60)) | (m60 <= 0) | (c >= m60 * (1.0 - P['MA60_TOL']))
    slope_ok = (~np.isfinite(s60)) | (s60 >= P['MA60_SLOPE_MIN'])
    R4 = s_ok & ma60_ok & slope_ok
    bh = st_.s_bounce_high[s, j].astype('float64')
    near = ((m20 > 0) & (c >= m20 * (1.0 - P['NEAR_TOL']))) | \
           ((bh > 0) & (c >= bh * (1.0 - P['NEAR_TOL'])))
    R5 = near
    rs20 = st_.rs_sector_20[s, j].astype('float64')
    R6 = np.isfinite(rs20) & (rs20 >= P['RS_MIN'])
    READY = R1 & R2 & R3 & R4 & R5 & R6

    # ---- FAILED bits -------------------------------------------------
    F1 = np.isfinite(all_min) & (all_min < lb_px * (1.0 - P['STRUCT_TOL']))
    n_b60 = _seg_count_b(st_.CS_b60, s, j - (P['F2_LOOKBACK'] - 1), j)
    F2 = n_b60 >= P['F2_MIN_DAYS']
    F3 = np.isfinite(s60) & (s60 < P['F3_SLOPE'])
    vr_mean = _seg_mean(st_.CS_vr, st_.CC_vr, s, peak_day + 1, j)
    dl = st_.cl < np.asarray(pd.DataFrame(st_.cl.astype('float64').T)
                             .shift(1).to_numpy().T, dtype='float64')
    down_hi = dl & (st_.vr20 >= P['F4_DOWN_VR'])
    n_down = _seg_count(down_hi, s, peak_day + 1, j)
    with np.errstate(invalid='ignore'):
        share_down = np.where(n_w > 0, n_down / n_w, np.nan)
    F4 = (np.isfinite(vr_mean) & (vr_mean >= P['F4_VR_MEAN'])
          & np.isfinite(share_down) & (share_down >= P['F4_SHARE']))
    rs_mean = _seg_mean(st_.CS_rs, st_.CC_rs, s, peak_day + 1, j)
    F5 = np.isfinite(rs_mean) & (rs_mean <= P['F5_RS_MEAN'])
    FAILED = F1 | F2 | F3 | F4 | F5
    del dl, down_hi

    out.update({'R1': R1, 'R2': R2, 'R3': R3, 'R4': R4, 'R5': R5, 'R6': R6,
                'READY': READY, 'F1': F1, 'F2': F2, 'F3': F3, 'F4': F4,
                'F5': F5, 'FAILED': FAILED,
                'vr_mean_consol': vr_mean, 'rs_mean_consol': rs_mean,
                'rb_age': rb_age.astype('float64'),
                'consol_days': (j - peak_day).astype('float64')})
    return out


def _seg_mean(CS, CC, s, a, b):
    ss = seg_sum(CS, s, a, b)
    cc = seg_sum(CC, s, a, b)
    with np.errstate(invalid='ignore'):
        return np.where(cc > 0, ss / cc, np.nan)


def _seg_count(M, s, a, b):
    C = np.cumsum(np.asarray(M, dtype='int32'), axis=1)
    return seg_sum(C, s, a, b).astype('float64')


def _seg_count_b(CS, s, a, b):
    b_ = np.clip(np.asarray(b), 0, CS.shape[1] - 1)
    hi = CS[s, b_].astype('float64')
    lo = np.zeros(s.size, dtype='float64')
    m = (np.asarray(a) - 1) >= 0
    if m.any():
        lo[m] = CS[s[m], np.clip(np.asarray(a)[m] - 1, 0, CS.shape[1] - 1)]
    return hi - lo


def _seg_min(M, s, a, b, W=41):
    """min over [a,b] inclusive (window length <= W). Chunked."""
    E = s.size
    out = np.full(E, np.nan)
    offs = np.arange(W)
    for k in range(0, E, 400000):
        e = min(E, k + 400000)
        aa = np.asarray(a)[k:e, None]
        bb = np.asarray(b)[k:e, None]
        cols = aa + offs[None, :]
        ok = cols <= bb
        cc = np.clip(cols, 0, M.shape[1] - 1)
        V = M[s[k:e, None], cc]
        V = np.where(ok & np.isfinite(V), V, np.nan)
        mn = np.nanmin(V, axis=1)
        out[k:e] = mn
    return out


# =====================================================================
# returns / metrics
# =====================================================================
def fwd_col(g, s, j, h, field='close', base='close'):
    N = g['close'].shape[1]
    a = np.asarray(j, dtype='int64')
    b = a + int(h)
    ok = (a >= 0) & (b <= N - 1)
    p0 = g[base][s, np.clip(a, 0, N - 1)].astype('float64')
    p1 = g[field][s, np.clip(b, 0, N - 1)].astype('float64')
    with np.errstate(invalid='ignore'):
        r = np.where((p0 > 0) & np.isfinite(p1), p1 / p0 - 1.0, np.nan)
    return np.where(ok, r, np.nan)


def stats(r, do_boot=False, months=None, B=None, seed=SEED):
    x = np.asarray(r, dtype='float64')
    keep = np.isfinite(x)
    x = x[keep]
    d = {'n': int(x.size)}
    keys = ('mean', 'median', 'win', 'std', 't', 'pf', 'p05', 'p95', 'max',
            'min', 'mean_win', 'mean_loss', 'tail_share')
    for k in keys:
        d[k] = np.nan
    for bp in COSTS:
        d['mean_net%d' % bp] = np.nan
    d['win_net%d' % COST_PRIMARY] = np.nan
    d['pf_net%d' % COST_PRIMARY] = np.nan
    if x.size == 0:
        return d
    d['n'] = int(x.size)
    d['mean'] = float(x.mean())
    d['median'] = float(np.median(x))
    d['win'] = float((x > 0).mean())
    gs = float(x[x > 0].sum())
    ls = float(-x[x < 0].sum())
    d['pf'] = (gs / ls) if ls > 0 else np.inf
    d['std'] = float(x.std(ddof=1)) if x.size > 1 else np.nan
    d['t'] = (d['mean'] / (d['std'] / np.sqrt(x.size))
              if x.size > 1 and d['std'] > 0 else np.nan)
    d['p05'] = float(np.percentile(x, 5))
    d['p95'] = float(np.percentile(x, 95))
    d['max'] = float(x.max())
    d['min'] = float(x.min())
    w = x[x > 0]
    l = x[x <= 0]
    d['mean_win'] = float(w.mean()) if w.size else np.nan
    d['mean_loss'] = float(l.mean()) if l.size else np.nan
    d['tail_share'] = (float(np.sort(x)[-max(1, int(x.size * 0.05)):].sum())
                       / max(1e-9, gs))
    for bp in COSTS:
        xn = x - bp / 10000.0
        d['mean_net%d' % bp] = float(xn.mean())
    xn = x - COST_PRIMARY / 10000.0
    d['win_net%d' % COST_PRIMARY] = float((xn > 0).mean())
    gs2 = float(xn[xn > 0].sum())
    ls2 = float(-xn[xn < 0].sum())
    d['pf_net%d' % COST_PRIMARY] = (gs2 / ls2) if ls2 > 0 else np.inf
    if do_boot and x.size >= 2:
        mm = None if months is None else np.asarray(months)[keep]
        b = boot_mean(x, mm, B=B or PREREG_S['boot_B'], seed=seed)
        d.update({'boot_lo': b['lo'], 'boot_hi': b['hi'],
                  'boot_p_le0': b['p_le0'], 'boot_n_month': b['n_month']})
    return d


def boot_mean(r, months, B=None, seed=SEED):
    B = B or PREREG_S['boot_B']
    x = np.asarray(r, dtype='float64')
    m = np.asarray(months) if months is not None else np.zeros(x.size, dtype=object)
    ok = np.isfinite(x)
    x, m = x[ok], m[ok]
    res = dict(mean=np.nan, lo=np.nan, hi=np.nan, p_le0=np.nan, n_month=0,
               n=int(x.size))
    if x.size < 2:
        return res
    if months is None:
        uq, inv = np.unique(np.arange(x.size) // 20, return_inverse=True)
    else:
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
               p_le0=float((means <= 0).mean()), n_month=int(k))
    return res


def diff_boot(ra, ma, rb, mb, B=None, seed=SEED):
    B = B or PREREG_S['boot_B']
    ra, rb = np.asarray(ra, 'float64'), np.asarray(rb, 'float64')
    oa, ob = np.isfinite(ra), np.isfinite(rb)
    ra, ma = ra[oa], np.asarray(ma)[oa]
    rb, mb = rb[ob], np.asarray(mb)[ob]
    out = dict(obs=np.nan, lo=np.nan, hi=np.nan, p_ge0=np.nan, n_month=0,
               n_a=int(ra.size), n_b=int(rb.size))
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
    ac, bc = ca[draws].sum(1), cb[draws].sum(1)
    asum, bsum = sa[draws].sum(1), sb[draws].sum(1)
    a_ = np.where(ac > 0, asum / np.maximum(ac, 1e-9), np.nan)
    b_ = np.where(bc > 0, bsum / np.maximum(bc, 1e-9), np.nan)
    dd = (a_ - b_)
    dd = dd[np.isfinite(dd)]
    out.update(obs=float(ra.mean() - rb.mean()),
               lo=float(np.percentile(dd, 2.5)) if dd.size else np.nan,
               hi=float(np.percentile(dd, 97.5)) if dd.size else np.nan,
               p_ge0=float((dd >= 0).mean()) if dd.size else np.nan,
               n_month=int(k))
    return out


# =====================================================================
# null model helpers (SPEC section 13)
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
        ok = ok & (uq[pp] == ev_key)
    else:
        ok = np.zeros(ev_key.size, bool)
    L = np.where(ok, lo[pp], 0)
    Hh = np.where(ok, hi[pp], 0)
    return L, Hh, ok


def draw_cells(L, Hh, flat, self_flat, rng, rounds):
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
        good = (v != self_flat[todo])
        out[todo[good]] = v[good]
        todo = todo[~good]
    return out


def null_family(rflat, flat, lo, hi, uq, ev_key, ev_self, obs, B=None,
                seed=SEED, rounds=None):
    B = B or PREREG_S['null_B']
    rounds = rounds or PREREG_S['null_rounds']
    L, Hh, okkey = pool_lookup(uq, lo, hi, ev_key)
    L = np.where(okkey, L, 0)
    Hh = np.where(okkey, Hh, 0)
    rng = np.random.default_rng(seed)
    means = np.full(B, np.nan)
    res = np.zeros(B)
    for b in range(B):
        v = draw_cells(L, Hh, flat, ev_self, rng, rounds)
        good = v >= 0
        res[b] = float(good.mean())
        if good.any():
            means[b] = float(np.nanmean(rflat[v[good]]))
    m = means[np.isfinite(means)]
    out = dict(obs=float(obs), B=int(B), resolution=float(res.mean()),
               key_coverage=float(okkey.mean()),
               null_mean=float(m.mean()) if m.size else np.nan,
               null_lo=float(np.percentile(m, 2.5)) if m.size else np.nan,
               null_hi=float(np.percentile(m, 97.5)) if m.size else np.nan,
               null_std=float(m.std(ddof=1)) if m.size > 1 else np.nan)
    out['excess'] = (out['obs'] - out['null_mean']
                     if np.isfinite(out['null_mean']) else np.nan)
    out['p_one_sided'] = float((m >= out['obs']).mean()) if m.size else np.nan
    return out


# =====================================================================
# regression / IC
# =====================================================================
def ols_fit(X, y):
    A = np.column_stack([np.ones(len(X)), X])
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    return beta, A


def r2_of(X, y, beta=None):
    if beta is None:
        beta, A = ols_fit(X, y)
    else:
        A = np.column_stack([np.ones(len(X)), X])
    pred = A.dot(beta)
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return (1.0 - ss_res / ss_tot) if ss_tot > 0 else np.nan, pred


def rank_ic(pred, y):
    p = pd.Series(pred)
    yy = pd.Series(y)
    m = np.isfinite(p) & np.isfinite(yy)
    if int(m.sum()) < 20:
        return np.nan
    return float(p[m].corr(yy[m], method='spearman'))


def ic_simple(pred, y):
    p = pd.Series(pred)
    yy = pd.Series(y)
    m = np.isfinite(p) & np.isfinite(yy)
    if int(m.sum()) < 20:
        return np.nan
    return float(p[m].corr(yy[m]))


def auc_score(score, label):
    s = np.asarray(score, 'float64')
    l = np.asarray(label).astype(bool)
    m = np.isfinite(s)
    s, l = s[m], l[m]
    if l.sum() == 0 or (~l).sum() == 0:
        return np.nan
    order = np.argsort(s)
    ranks = np.empty(s.size, dtype='float64')
    ranks[order] = np.arange(1, s.size + 1)
    # average ranks for ties
    sr = s[order]
    i = 0
    while i < sr.size:
        jx = i
        while jx + 1 < sr.size and sr[jx + 1] == sr[i]:
            jx += 1
        if jx > i:
            avg = (i + jx + 2) / 2.0
            ranks[order[i:jx + 1]] = avg
        i = jx + 1
    n1 = int(l.sum())
    n0 = int((~l).sum())
    return float((ranks[l].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))
