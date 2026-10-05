# -*- coding: utf-8 -*-
"""H-SLG-02 -- common layer: factor / IC / overlap / null primitives.

Every rule here is transcribed from H_SLG_02_SPEC.md (v1.0, SHA256 frozen in
H_SLG_02_FREEZE.md) and was written before any result was observed.

Chain under study (SPEC section 0): the *completion* of a post-surge
consolidation is treated as a CONTINUOUS cross-sectional factor inside the
pool ``U_FACTOR = CTX & R1``.  ``READY`` / ``FAILED`` are never used to
filter the pool (SPEC 6.2.1 / 6.2.2).

``slg_common`` (H-SLG-01) is reused READ-ONLY: this module never edits it, and
never edits any ``H_SLG_01_*`` artefact (SPEC 22.8 / FREEZE 6.6).

Runtime note: the parameter grid needs structures with ``leg_w`` in
{30,40,50} and ``consol_max`` up to 50.  ``build_structure`` reads the frozen
``slg_common.PREREG_S`` dict, so the widest window is built once and every
smaller ``consol_max`` is obtained by masking ``consol_days`` (for a cell with
``consol_days <= c`` the wider search window is provably identical).
"""
import os
import sys
import copy
import math

import numpy as np

import slg_common as S
import hve_common as H

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = HERE
SPEC2 = os.path.join(HERE, 'H_SLG_02_SPEC.md')
FREEZE2 = os.path.join(HERE, 'H_SLG_02_FREEZE.md')
RESULTS2 = os.path.join(HERE, 'H_SLG_02_RESULTS.json')

# Frozen SPEC hash -- mismatch means the pre-registration was edited after the
# freeze, so the experiment is void (SPEC 21 / 22.1, FREEZE 1 / 6.1).
SPEC2_SHA256 = 'DEFAF8D88E69810B96E8EB5419E7727C22C5A17B2C32A39AFC0BB6D16826410E'
SPEC2_BYTES = 21579

# =====================================================================
# PREREG2 -- EVERY threshold lives here, nowhere else (SPEC 22.2)
# =====================================================================
PREREG2 = {
    'hypothesis_id': 'H-SLG-02',
    'version': '1.0',
    'created': '2026-10-03',
    'prior_study': 'H-SLG-01',
    'title': 'post-surge consolidation completion as a continuous '
             'cross-sectional factor',

    # ---- sample / phases (SPEC 3) ------------------------------------
    'study_start': '20190101',
    'burn_in_sessions': 250,
    'phase_IS': (2020, 2024),
    'phase_VALID': (2025, 2025),
    'phase_OOS': (2026, 2026),
    'wf_train_years': 3,
    'wf_test_years': 1,
    'wf_start_year': 2021,
    'wf_end_year': 2026,

    # ---- causal structure (SPEC 5, reused verbatim from H-SLG-01 5) --
    'LEG_W': 40,
    'CONS_MIN': 5,
    'CONS_MAX': 40,
    'PEAK_LO': 5,
    'PEAK_HI': 45,
    'first_rise_pct': 0.25,
    'first_rise_days': 5,
    'retrace_min': 0.05,
    # structure is built once with the WIDEST windows and masked down
    'struct_leg_w_max': 50,
    'struct_consol_max': 50,

    # ---- horizons / quantiles ----------------------------------------
    'horizons': (3, 5, 10, 20),
    'h_star': 5,
    'Q_GROUPS': 5,
    'reg_h2': 10,

    # ---- IC gates / monotonicity (SPEC 8.5, 10.3, 14.3) --------------
    'IC_MIN': 0.01,
    'T_MIN': 2.0,
    'MONO_MIN': 0.80,
    'T_WF_MIN': 1.5,
    'MIN_XS_N': 20,
    'NW_LAG': 5,
    'DEDUP_GAP': 5,
    'DEDUP_MIN_N': 5,

    # ---- parameter stability (SPEC 15) -------------------------------
    'PARAM_IC_FLOOR': 0.005,
    'param_stable_thr': 0.60,
    'param_fragile_thr': 0.40,
    'RETAIN_THR': 0.70,

    # ---- costs (SPEC 10.2) -------------------------------------------
    'cost_bp': (0, 15, 30, 50),
    'primary_cost_bp': 30,

    # ---- null (SPEC 13) ----------------------------------------------
    'null_B': 2000,
    'null_rounds': 8,
    'null_min_resolution': 0.99,

    # ---- pre-registered perturbation grid (SPEC 15) ------------------
    'grid_leg_w': (30, 40, 50),
    'grid_consol_max': (20, 30, 40, 50),
    'grid_retrace_min': (0.05, 0.10, 0.15),
    'grid_first_rise_pct': (0.20, 0.25, 0.30),
    'grid_q_groups': (5, 10),

    # ---- subgroup breakpoints (SPEC 16) ------------------------------
    'sub_retr_cuts': (0.10, 0.20, 0.30),
    'sub_vol_cuts': (0.8, 1.2),

    'seed': 20261103,
    'trading_authorization': 'NO',
}

SEED2 = PREREG2['seed']
HORIZONS2 = PREREG2['horizons']
HSTAR2 = PREREG2['h_star']
COSTS2 = PREREG2['cost_bp']
COST_PRIMARY2 = PREREG2['primary_cost_bp']

# ---- 16 factors, pre-registered direction (SPEC 7, FREEZE 5.1) -------
# (id, field name, expected sign of "more complete")
FACTOR_SPEC = (
    ('P1', 'vol_contract_rb', '-'),
    ('P2', 'vol_contract_consol', '-'),
    ('P3', 'vol_ratio_5_leg', '-'),
    ('P4', 'atr_ratio', '-'),
    ('P5', 'ma20_pos', '+'),
    ('P6', 'ma20_slope', '+'),
    ('P7', 'ma20_hold', '+'),
    ('P8', 'ma60_pos', '+'),
    ('P9', 'ma60_slope', '+'),
    ('P10', 'rb_quality', '+'),
    ('P11', 'rb_above_ma20', '+'),
    ('P12', 'near_neckline', '+'),
    ('P13', 'rb_hold', '+'),
    ('P14', 'rs_sector', '+'),
    ('P15', 'ready_count', '+'),
    ('P16', 'ready_dummy', '+'),
)
FACTOR_IDS = tuple(k for k, _, _ in FACTOR_SPEC)
FACTOR_NAME = {k: n for k, n, _ in FACTOR_SPEC}
FACTOR_DIR = {k: d for k, _, d in FACTOR_SPEC}
CONT_FACTORS = tuple(k for k in FACTOR_IDS if k not in ('P15', 'P16'))
COMP_FACTORS = tuple('P%d' % i for i in range(1, 15))

# label columns this module consumes (registered in slg_common.LABEL_COLUMNS)
LABEL_KEYS2 = tuple(
    ['fwd_ret_%d' % h for h in HORIZONS2]
    + ['fwd_ret_mkt_%d' % h for h in HORIZONS2]
    + ['fwd_ret_sec_%d' % h for h in HORIZONS2])


# =====================================================================
# spec verification / phases / small helpers
# =====================================================================
def verify_spec2(lg=None):
    """SPEC 21: hash mismatch -> caller must EXIT 2."""
    if not os.path.exists(SPEC2):
        if lg:
            lg('FATAL: %s missing' % SPEC2)
        return False
    got = S.sha256(SPEC2)
    nb = int(os.path.getsize(SPEC2))
    if got != SPEC2_SHA256 or nb != SPEC2_BYTES:
        msg = ('SPEC HASH/SIZE MISMATCH\n  expected %s / %d bytes\n'
               '  actual   %s / %d bytes\n'
               'The pre-registration changed after the freeze; experiment '
               'is void.' % (SPEC2_SHA256, SPEC2_BYTES, got, nb))
        if lg:
            lg(msg)
        return False
    if lg:
        lg('SPEC hash verified (%s / %d bytes)' % (got[:16], nb))
    return True


def phase_of2(d):
    y = int(str(d)[:4])
    if y <= 2019:
        return 'PRE'
    if y <= 2024:
        return 'IS'
    if y == 2025:
        return 'VALID'
    return 'OOS'


def dir_sign(k):
    """+1 when the factor value rises with completion, -1 when it falls."""
    return 1.0 if FACTOR_DIR[k] == '+' else -1.0


def plain_t(x):
    x = np.asarray(x, dtype='float64')
    x = x[np.isfinite(x)]
    if x.size < 2:
        return np.nan
    sd = float(x.std(ddof=1))
    if sd <= 0:
        return np.nan
    return float(x.mean() / (sd / math.sqrt(x.size)))


def nw_t(x, lag=None):
    lag = PREREG2['NW_LAG'] if lag is None else int(lag)
    mu, t, icir, n = H.ic_stats(np.asarray(x, dtype='float64'), lag)
    return float(mu), float(t), float(icir), int(n)


# =====================================================================
# panel / structure (widest windows, masked down afterwards)
# =====================================================================
def load_state(g, lg=None, trunc=0):
    return S.State(g, lg, trunc=trunc)


def build_structure2(st, leg_w, consol_max=None, lg=None):
    """Causal structure with a widened leg window.

    ``slg_common.build_structure`` is reused verbatim; the frozen PREREG_S is
    temporarily widened (never edited on disk) so that every grid point can be
    derived from one build (SPEC 15.2).
    """
    P = S.PREREG_S
    keep_lw = P['LEG_W']
    keep_cm = P['CONS_MAX']
    P['LEG_W'] = int(leg_w)
    P['CONS_MAX'] = int(PREREG2['struct_consol_max']
                        if consol_max is None else consol_max)
    try:
        if lg:
            lg('build_structure leg_w=%d consol_max=%d'
               % (P['LEG_W'], P['CONS_MAX']))
        return S.build_structure(st.hi, st.lo)
    finally:
        P['LEG_W'] = keep_lw
        P['CONS_MAX'] = keep_cm


def struct_view(st, struct):
    """Shallow copy of ``st`` whose causal-structure arrays are replaced."""
    v = copy.copy(st)
    for k, arr in struct.items():
        setattr(v, 's_' + k, arr)
    return v


def pool_mask(st, struct, consol_max, first_rise_pct, retrace_min):
    """``CTX`` under a grid point (SPEC 5 + 6).  R1 is applied separately."""
    po = struct['peak_off'].astype('int64')
    frp = struct['first_rise_pct']
    frd = struct['first_rise_days']
    retr = struct['retracement_pct']
    with np.errstate(invalid='ignore'):
        m = (st.el & struct['v_peak'] & struct['v_leg'] & struct['v_bounce']
             & (frp >= first_rise_pct)
             & (frd >= PREREG2['first_rise_days'])
             & np.isfinite(retr) & (retr >= retrace_min)
             & (po >= PREREG2['CONS_MIN']) & (po <= int(consol_max)))
    m[:, :st.s0] = False
    return np.ascontiguousarray(m)


def cells_from_mask(m):
    """Flat cells sorted by (day, stock) + the day index."""
    s, j = np.nonzero(m)
    order = np.lexsort((s, j))
    s = s[order].astype('int64')
    j = j[order].astype('int64')
    uj, day = np.unique(j, return_inverse=True)
    return s, j, day.astype('int64'), uj


def episode_first(mask):
    """First cell of every 整合理期 cluster (SPEC 9.1), same mask as pool."""
    return H.cluster_events(mask, int(PREREG2['DEDUP_GAP']))


# =====================================================================
# factor layer (SPEC 7)
# =====================================================================
def factor_vars2(stv, s, j):
    """P1..P16 for flat cells, every one causally defined at t."""
    cv = S.completion_vars(stv, s, j)
    cl = stv.cl[s, j].astype('float64')
    ne = stv.s_neckline[s, j].astype('float64')
    with np.errstate(invalid='ignore'):
        near_neck = np.where(ne > 0, cl / ne - 1.0, np.nan)
        rb_hold = np.where(cv['consol_days'] > 0,
                           cv['rb_age'] / cv['consol_days'], np.nan)
    rc = np.zeros(s.size, dtype='float64')
    for b in ('R1', 'R2', 'R3', 'R4', 'R5', 'R6'):
        rc += cv[b].astype('float64')
    out = {
        'P1': np.asarray(cv['rb_vol_over_ma20'], dtype='float64'),
        'P2': np.asarray(cv['consol_vol_over_leg'], dtype='float64'),
        'P3': np.asarray(cv['vol_ratio_5_leg'], dtype='float64'),
        'P4': np.asarray(cv['atr_ratio'], dtype='float64'),
        'P5': np.asarray(cv['close_vs_ma20'], dtype='float64'),
        'P6': np.asarray(cv['ma20_slope20'], dtype='float64'),
        'P7': 1.0 - np.asarray(cv['frac_below_ma20'], dtype='float64'),
        'P8': np.asarray(cv['close_vs_ma60'], dtype='float64'),
        'P9': np.asarray(cv['ma60_slope20'], dtype='float64'),
        'P10': np.asarray(cv['rb_dist_left_bottom'], dtype='float64'),
        'P11': np.asarray(cv['rb_dist_ma20'], dtype='float64'),
        'P12': near_neck,
        'P13': rb_hold,
        'P14': stv.rs_sector_20[s, j].astype('float64'),
        'P15': rc,
        'P16': cv['READY'].astype('float64'),
    }
    return out, cv


def compute_factors(stv, cs, cj, chunk=200000, lg=None, tag='factors'):
    C = cs.size
    out = {k: np.full(C, np.nan, dtype='float32') for k in FACTOR_IDS}
    for a in range(0, C, chunk):
        b = min(C, a + chunk)
        fv, _ = factor_vars2(stv, cs[a:b], cj[a:b])
        for k in FACTOR_IDS:
            out[k][a:b] = fv[k].astype('float32')
        if lg:
            lg('  %s %d/%d' % (tag, b, C))
    return out


# =====================================================================
# labels (SPEC 8.2) -- the ONLY place t > T information is consumed
# =====================================================================
def _fwd_panel(cl, h):
    S_, N = cl.shape
    Y = np.full((S_, N), np.nan, dtype='float32')
    a = cl[:, :N - h].astype('float64')
    b = cl[:, h:].astype('float64')
    with np.errstate(invalid='ignore'):
        Y[:, :N - h] = np.where((a > 0) & np.isfinite(b), b / a - 1.0,
                                np.nan).astype('float32')
    return Y


def sec_mean_panel(st, h):
    """Same-window, same-industry equal-weight mean of y_h (SPEC 8.2)."""
    Y = _fwd_panel(st.cl, h)
    n_l1 = len(st.l1s)
    out = np.full((st.S, st.N), np.nan, dtype='float32')
    for t in range(st.N):
        m = st.el[:, t] & (st.ind[:, t] >= 0) & np.isfinite(Y[:, t])
        if not m.any():
            continue
        jj = st.ind[m, t].astype('int64')
        vv = Y[m, t].astype('float64')
        ssum = np.bincount(jj, weights=vv, minlength=n_l1)
        cnt = np.bincount(jj, minlength=n_l1).astype('float64')
        mu = np.where(cnt > 0, ssum / np.maximum(cnt, 1e-9), np.nan)
        out[m, t] = mu[jj].astype('float32')
    return out


def mkt_index_cum(st):
    mkt = np.asarray(st.mkt_ret, dtype='float64')
    bad = ~np.isfinite(mkt)
    lr = np.log1p(np.nan_to_num(mkt, nan=0.0))
    cl_ = np.concatenate([[0.0], np.cumsum(lr)])
    cb_ = np.concatenate([[0], np.cumsum(bad.astype('int64'))])
    return cl_, cb_


def build_labels(st, cs, cj, lg=None):
    """y_abs / y_ex_mkt / y_ex_sec for every horizon (SPEC 8.2)."""
    N = st.N
    cl_, cb_ = mkt_index_cum(st)
    res = {}
    for h in HORIZONS2:
        Y = _fwd_panel(st.cl, h)
        y_abs = Y[cs, cj].astype('float64')
        j2 = cj + h
        ok = j2 <= N - 1
        jc = np.clip(j2, 0, N - 1)
        with np.errstate(invalid='ignore'):
            ir = np.exp(cl_[jc + 1] - cl_[cj + 1]) - 1.0
        nbad = cb_[jc + 1] - cb_[cj + 1]
        y_mkt = np.where(ok & (nbad == 0), y_abs - ir, np.nan)
        sm = sec_mean_panel(st, h)
        y_sec = y_abs - sm[cs, cj].astype('float64')
        y_sec = np.where(np.isfinite(sm[cs, cj]), y_sec, np.nan)
        res[h] = {'abs': y_abs, 'mkt': y_mkt, 'sec': y_sec}
        if lg:
            lg('  label h=%d  finite sec %.4f' % (h, float(np.isfinite(y_sec).mean())))
        del Y, sm
    return res


def fwd_ret_cells(st, cs, cj, h):
    """close[t+h]/close[t]-1 on flat cells (used for cost / quantile work)."""
    N = st.N
    a = cj.astype('int64')
    b = a + int(h)
    ok = b <= N - 1
    p0 = st.cl[cs, np.clip(a, 0, N - 1)].astype('float64')
    p1 = st.cl[cs, np.clip(b, 0, N - 1)].astype('float64')
    with np.errstate(invalid='ignore'):
        r = np.where((p0 > 0) & np.isfinite(p1), p1 / p0 - 1.0, np.nan)
    return np.where(ok, r, np.nan)


# =====================================================================
# cross-sectional ranks and daily IC (SPEC 8.1 / 8.3 / 8.4)
# =====================================================================
def day_rank_pct(v, day):
    """rank/(n+1) inside each day; NaN preserved (SPEC 8.1)."""
    v = np.asarray(v, dtype='float64')
    out = np.full(v.size, np.nan)
    m = np.isfinite(v)
    if not m.any():
        return out
    idx = np.flatnonzero(m)
    vv = v[idx]
    dd = day[idx]
    order = np.lexsort((vv, dd))
    dv = dd[order]
    sv = vv[order]
    uq, starts = np.unique(dv, return_index=True)
    ends = np.r_[starts[1:], dv.size]
    grp = np.searchsorted(uq, dv)
    n = (ends - starts)[grp].astype('float64')
    r = np.arange(1, dv.size + 1, dtype='float64')
    newg = np.empty(dv.size, dtype=bool)
    newg[0] = True
    if dv.size > 1:
        newg[1:] = (dv[1:] != dv[:-1]) | (sv[1:] != sv[:-1])
    gid = np.cumsum(newg) - 1
    gs = np.bincount(gid, weights=r)
    gc = np.bincount(gid)
    ravg = gs[gid] / gc[gid]
    out[idx[order]] = ravg / (n + 1.0)
    return out


def day_ic(rf, ry, day, D, min_n=None):
    """Daily Pearson correlation of the two cross-sectional rank vectors."""
    min_n = PREREG2['MIN_XS_N'] if min_n is None else int(min_n)
    m = np.isfinite(rf) & np.isfinite(ry)
    di = day[m]
    a = rf[m]
    b = ry[m]
    cnt = np.bincount(di, minlength=D).astype('float64')
    ma = np.bincount(di, weights=a, minlength=D) / np.maximum(cnt, 1.0)
    mb = np.bincount(di, weights=b, minlength=D) / np.maximum(cnt, 1.0)
    ac = a - ma[di]
    bc = b - mb[di]
    num = np.bincount(di, weights=ac * bc, minlength=D)
    va = np.bincount(di, weights=ac * ac, minlength=D)
    vb = np.bincount(di, weights=bc * bc, minlength=D)
    den = np.sqrt(va * vb)
    with np.errstate(divide='ignore', invalid='ignore'):
        ic = np.where((den > 0) & (cnt >= min_n), num / den, np.nan)
    return ic, cnt


def ic_summary(ic, cnt, days, phases, nw=True):
    """SPEC 8.4 statistics overall and per phase."""
    def _one(sel):
        x = ic[sel]
        x = x[np.isfinite(x)]
        d = {'ic_mean': np.nan, 'ic_std': np.nan, 'icir': np.nan,
             't_nw': np.nan, 'pos_ratio': np.nan, 'avg_n': np.nan,
             'days': int(x.size)}
        if x.size == 0:
            return d
        mu, t, icir, n = nw_t(x, PREREG2['NW_LAG']) if nw else (
            float(x.mean()), plain_t(x), np.nan, int(x.size))
        d['ic_mean'] = mu
        d['ic_std'] = float(x.std(ddof=1)) if x.size > 1 else np.nan
        d['icir'] = icir
        d['t_nw'] = t
        d['pos_ratio'] = float((x > 0).mean())
        cc = cnt[sel]
        cc = cc[np.isfinite(ic[sel])]
        d['avg_n'] = float(cc.mean()) if cc.size else np.nan
        d['days'] = int(x.size)
        return d
    allsel = np.ones(ic.size, dtype=bool)
    out = {'ALL': _one(allsel)}
    for ph in ('IS', 'VALID', 'OOS', 'PRE'):
        out[ph] = _one(phases == ph)
    return out


# =====================================================================
# composites (SPEC 11) -- equal weight only, never optimised
# =====================================================================
def composite(rk, ids, signs):
    """Equal-weight mean of direction-aligned rank vectors (SPEC 11.1/11.4)."""
    if len(ids) == 0:
        return None
    acc = np.zeros(rk[ids[0]].size, dtype='float64')
    cnt = np.zeros(rk[ids[0]].size, dtype='float64')
    for k, sg in zip(ids, signs):
        v = rk[k] * float(sg)
        m = np.isfinite(v)
        acc[m] += v[m]
        cnt[m] += 1.0
    with np.errstate(invalid='ignore'):
        out = np.where(cnt > 0, acc / np.maximum(cnt, 1e-9), np.nan)
    return out


# =====================================================================
# null models (SPEC 13)
# =====================================================================
def cyc_ic_matrix(rf, ry, day, D, min_n=None):
    """All within-day cyclic shifts of the rank vector -> daily IC values.

    Shifting the factor's rank vector inside a day is a randomisation over a
    subgroup of the day's permutations; under the exchangeability null it is
    an exact randomisation test (SPEC 13 N1 / N2).  ``M[d, 0]`` is the
    observed daily IC, so the observed statistic needs no second pass.
    """
    min_n = PREREG2['MIN_XS_N'] if min_n is None else int(min_n)
    m = np.isfinite(rf) & np.isfinite(ry)
    di = day[m]
    a = rf[m].astype('float64')
    b = ry[m].astype('float64')
    cnt = np.bincount(di, minlength=D).astype('float64')
    ma = np.bincount(di, weights=a, minlength=D) / np.maximum(cnt, 1.0)
    mb = np.bincount(di, weights=b, minlength=D) / np.maximum(cnt, 1.0)
    ac = a - ma[di]
    bc = b - mb[di]
    nmax = int(cnt.max()) if cnt.size else 0
    M = np.zeros((D, max(nmax, 1)), dtype='float64')
    n_arr = cnt.astype('int64')
    order = np.argsort(di, kind='stable')
    A = ac[order]
    B = bc[order]
    starts = np.searchsorted(di[order], np.arange(D), side='left')
    ends = np.searchsorted(di[order], np.arange(D), side='right')
    for d in range(D):
        n = int(ends[d] - starts[d])
        if n < max(min_n, 2):
            continue
        av = A[starts[d]:ends[d]]
        bv = B[starts[d]:ends[d]]
        na = float(np.sqrt(av @ av))
        nb = float(np.sqrt(bv @ bv))
        if na <= 0 or nb <= 0:
            continue
        nf = 1 << int(math.ceil(math.log2(2 * n)))
        c = np.fft.irfft(np.fft.rfft(av, nf) * np.conj(np.fft.rfft(bv, nf)),
                         nf)[:n]
        M[d, :n] = c / (na * nb)
    return M, n_arr


def null_from_matrix(M, n_arr, rng, B):
    """Draw one random shift per day (SPEC 13 N1), mean over valid days."""
    D = M.shape[0]
    valid = n_arr >= int(PREREG2['MIN_XS_N'])
    nd = int(valid.sum())
    if nd == 0:
        return np.full(B, np.nan), 0
    tot = np.zeros(B, dtype='float64')
    for d in range(D):
        n = int(n_arr[d])
        if n < int(PREREG2['MIN_XS_N']):
            continue
        rr = rng.integers(0, n, size=B)
        tot += M[d, rr]
    return tot / float(nd), nd


def null_report(obs, draws):
    draws = np.asarray(draws, dtype='float64')
    draws = draws[np.isfinite(draws)]
    d = {'obs': float(obs), 'B': int(draws.size), 'null_mean': np.nan,
         'null_lo': np.nan, 'null_hi': np.nan, 'null_std': np.nan,
         'p_one_sided': np.nan, 'resolution': 1.0}
    if draws.size == 0:
        return d
    d['null_mean'] = float(draws.mean())
    d['null_lo'] = float(np.percentile(draws, 2.5))
    d['null_hi'] = float(np.percentile(draws, 97.5))
    d['null_std'] = float(draws.std(ddof=1)) if draws.size > 1 else np.nan
    d['p_one_sided'] = float((draws >= obs).mean())
    return d


# =====================================================================
# forward-window scan (SPEC 21) -- any failure -> caller EXIT 3
# =====================================================================
def _eq_nan(a, b):
    a, b = np.asarray(a), np.asarray(b)
    if a.shape != b.shape:
        return False
    fa, fb = np.isfinite(a), np.isfinite(b)
    if not np.array_equal(fa, fb):
        return False
    return bool(np.array_equal(a[fa], b[fb]))


def future_column_scan2(g, st, cs, cj, label_keys, lg=None):
    P2 = PREREG2
    res = {}

    # 1 truncation invariance ------------------------------------------
    gt = S.truncate(g, 120)
    i0 = int(np.searchsorted(gt['dates'], P2['study_start']))
    c0 = max(0, i0 - int(P2['burn_in_sessions']))
    sl = slice(c0, gt['close'].shape[1])
    hh = np.ascontiguousarray(gt['high'][:, sl])
    ll = np.ascontiguousarray(gt['low'][:, sl])
    # rebuild the truncated panel with EXACTLY the windows the main state was
    # built with (frozen PREREG_S), otherwise the two builds are not comparable
    st_t = build_structure2(_MinState(hh, ll), S.PREREG_S['LEG_W'],
                            consol_max=S.PREREG_S['CONS_MAX'])
    n_cmp = st_t['peak_off'].shape[1]
    pairs = [('peak_off', st.s_peak_off), ('leg_off', st.s_leg_off),
             ('bounce_off', st.s_bounce_off), ('lb_off', st.s_lb_off),
             ('rb_off', st.s_rb_off), ('first_rise_days', st.s_first_rise_days),
             ('first_rise_pct', st.s_first_rise_pct),
             ('retracement_pct', st.s_retracement_pct),
             ('right_left_ratio', st.s_right_left_ratio),
             ('neckline', st.s_neckline), ('struct_type', st.s_struct_type)]
    bad = [k for k, a in pairs if not _eq_nan(a[:, :n_cmp], st_t[k])]
    res['truncation_invariance'] = (
        len(bad) == 0, 'cols=%d mismatched=%s' % (n_cmp, bad or 'none'))

    # 2 backward window proof ------------------------------------------
    k = min(cs.size, 20000)
    s_, j_ = cs[:k], cj[:k]
    po = st.s_peak_off[s_, j_].astype('int64')
    ne = st.s_neckline[s_, j_].astype('float64')
    hp = st.hi[s_, j_ - po].astype('float64')
    ok_lag = bool((po >= P2['CONS_MIN']).all()) if po.size else False
    ok_px = bool(np.allclose(ne, hp, rtol=0, atol=1e-5, equal_nan=True))
    res['backward_window_proof'] = (
        ok_lag and ok_px,
        'min_peak_off=%d  neckline==high[peak_day]=%s (n=%d)'
        % (int(po.min()) if po.size else -1, ok_px, k))

    # 3 forward window isolation ---------------------------------------
    inter = set(S.SIGNAL_COLUMNS) & set(S.LABEL_COLUMNS)
    res['forward_window_isolation'] = (
        len(inter) == 0, 'signal/label intersection=%s' % (sorted(inter) or 'empty'))

    # 4 label columns marked -------------------------------------------
    unmarked = [c for c in label_keys if c not in S.LABEL_COLUMNS]
    res['label_columns_marked'] = (len(unmarked) == 0,
                                   'unmarked=%s' % (unmarked or 'none'))
    ok = True
    for kk in ('truncation_invariance', 'backward_window_proof',
               'forward_window_isolation', 'label_columns_marked'):
        good, detail = res[kk]
        ok &= bool(good)
        if lg:
            lg('  %-26s %-4s  %s' % (kk, 'OK' if good else 'FAIL', detail))
    return ok, res


class _MinState(object):
    """Minimal carrier so build_structure can be re-run on a truncated panel."""

    def __init__(self, hi, lo):
        self.hi = hi
        self.lo = lo
        self.s_peak_off = None
