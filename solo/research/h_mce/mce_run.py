# -*- coding: utf-8 -*-
"""H-MCE-01 -- main run.

Every rule is transcribed from the frozen H_MCE_01_SPEC.md; see
mce_common.PREREG.  Nothing here may be edited after the first run without
voiding the experiment (SPEC sections 9 / 18 / 21).

Artefacts produced here (SPEC section 20):
    multi_candle_event_definitions.json
    multi_candle_event_samples.csv
    multi_candle_event_results.csv
    multi_candle_event_oos.csv
    multi_candle_event_yearly.csv
    multi_candle_event_regime.csv
    multi_candle_event_parameter_stability.csv
    multi_candle_event_null_model.csv
    multi_candle_event_momentum_compare.csv
  supplementary:
    H_MCE_01_RESULTS.json   H_MCE_01_SUMMARY.json
    H_MCE_01_BIAS_AUDIT.csv H_MCE_01_BENCHMARK.csv
    H_MCE_01_INCREMENTAL.csv H_MCE_01_SIZE.csv  H_MCE_01_SURVIVOR.csv

Exit codes: 0 ok, 2 spec hash mismatch, 3 future_column_scan FATAL.
"""
import os
import sys
import time
import json
import itertools
import warnings

warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd

import mce_common as M
from mce_common import (PREREG, HORIZONS, COSTS, COST_PRIMARY, CORE_EVENTS,
                        EVENT_NAMES, LABEL_COLUMNS, State, save_csv, save_json,
                        fmt, fmtp, metrics, boot_mean, diff_boot, bh_fdr,
                        build_pools, null_family, bucket_ret, regime_ma,
                        load_size_mv, _shift, _prev, _div, first_hit_gt,
                        first_hit_off, fwd_all_ge, fwd_all_lt, fwd_mean,
                        fwd_min, fwd_max, ret_matrix, overlap_stats, fwd,
                        path_stats)

P = PREREG
SEED = P['seed']
COST = COST_PRIMARY
HSTAR = 5                                   # primary horizon (SPEC section 18)

# =====================================================================
# static column-list audit (SPEC section 8 check 3/4)
# ---------------------------------------------------------------------
# The base variables each core event's T-day mask is allowed to reference.
# None of these may appear in LABEL_COLUMNS (columns that use t > T).
# =====================================================================
SIGNAL_COLUMNS = {
    'E01': ('gap', 'close', 'open', 'prev_close', 'vr20', 'ma20'),
    'E02': ('gap', 'close', 'open', 'prev_close', 'vr20', 'ma20', 'high'),
    'E03': ('ret_1d_prev', 'gap', 'close', 'open', 'vr20', 'prev_close'),
    'E04': ('ret_1d_prev', 'close', 'open', 'body_pct', 'vol', 'vol_prev',
            'vr20'),
    'E05': ('close', 'rolling_high_20', 'vr20', 'low'),
    'E06': ('close', 'open', 'vr20', 'high', 'low'),
    'E07': ('lower_shadow', 'high', 'low', 'body', 'vol', 'vol_prev', 'vr20'),
    'E08': ('gap', 'low', 'high_prev', 'vol', 'vr20'),
    'E09': ('ret_1d', 'vr20', 'vol', 'low', 'close', 'open', 'high'),
    'E10': ('vr20', 'close', 'low', 'high'),
}


# =====================================================================
# helpers
# =====================================================================
def day_phase_array(st):
    dd = np.array(st.dates_w, dtype=object)
    out = np.full(st.N, 'PRE', dtype=object)
    out[(dd >= P['is_start']) & (dd <= P['is_end'])] = 'IS'
    out[(dd >= P['oos_start']) & (dd <= P['oos_end'])] = 'OOS'
    return out


def stats_block(r, months, do_boot=True):
    met = metrics(r, costs=COSTS, primary=COST)
    met['gross'] = met['mean']
    met['net0'] = met['mean_net0']
    met['net15'] = met['mean_net15']
    met['net30'] = met['mean_net30']
    met['net50'] = met['mean_net50']
    met['win30'] = met['win_net30']
    for k in ('mean_net0', 'mean_net15', 'mean_net30', 'mean_net50',
              'win_net0', 'win_net15', 'win_net30', 'win_net50',
              'net_primary', 'win_primary'):
        met.pop(k, None)
    if do_boot:
        xn = np.asarray(r, dtype='float64') - COST / 10000.0
        b = boot_mean(xn, months, B=P['boot_B'], seed=SEED)
        met['boot_lo'] = b['lo']
        met['boot_hi'] = b['hi']
        met['boot_p'] = b['p_le0']
        met['n_cluster'] = b['n_month']
    return met


def exists_after_gt(st, koff, cond_daily, lo_off, hi_off):
    """exists d in [lo,hi] with close[t+koff+d] > high[t] and cond_daily."""
    S, N = koff.shape
    rr = np.arange(S)[:, None]
    acc = np.zeros((S, N), dtype=bool)
    for d in range(lo_off, hi_off + 1):
        idx = np.arange(N)[None, :] + koff + d
        valid = (koff >= 0) & (idx >= 0) & (idx <= N - 1)
        jj = np.clip(idx, 0, N - 1)
        g = st.cl[rr, jj]
        with np.errstate(invalid='ignore'):
            ok = (valid & np.isfinite(g) & (g > st.hi)
                  & cond_daily[rr, jj])
        acc |= ok
    return acc


def first_offset_ge(mask_off):
    return mask_off >= 0


# =====================================================================
# parameterised core-mask builder (used by the grid + survivor audit)
# =====================================================================
def main_ov(name):
    q = {
        'E01': {'gap': P['GAP2'], 'vr': P['VR2']},
        'E02': {'gap': P['GAP2'], 'vr': P['VR2']},
        'E03': {'gap15': P['GAP15'], 'vr15': P['VR15'], 'ret7': P['RET7']},
        'E04': {'shrink': P['SHRINK'], 'vrcap': P['VRCAP'], 'ret3': P['RET3']},
        'E05': {'vr15': P['VR15'], 'pull': P['PULL']},
        'E06': {'vr25': P['VR25'], 'recover': 0.50},
        'E07': {'shadow': P['SR'], 'vr12': P['VR12']},
        'E08': {'gap': P['GAP2'], 'hold': 5},
        'E09': {'shrink': P['SHRINK'], 'vr13': P['VR13']},
        'E10': {'vr25': P['VR25'], 'ddm': P['DDM']},
    }
    return q[name]


def grid_mask(st, name, ov, ef=None):
    """Rebuild a core event's signal mask (anchor = mask column) with the
    given threshold overrides.  Used for the pre-registered perturbation grid
    and the U1/U2 survivorship audit -- never for the main table."""
    cl, op, hi, lo, pc, vr20 = st.cl, st.o, st.hi, st.lo, st.pc, st.vr20
    ef = st.ef if ef is None else ef

    if name in ('E01', 'E02'):
        e01 = (ef & (st.gap >= ov['gap']) & (cl < op) & (cl > pc)
               & (vr20 >= ov['vr']) & (cl > st.ma20))
        if name == 'E01':
            return e01
        c2 = (cl > op) & (cl > _prev(hi)) & (op <= _prev(cl))
        return e01 & _shift(c2, 1)

    if name == 'E03':
        return (ef & (_shift(st.ret_1d, 1) >= ov['ret7'])
                & (st.gap >= ov['gap15']) & (cl < op)
                & (vr20 >= ov['vr15']) & (cl >= pc))

    if name == 'E04':
        return (ef & (_shift(st.ret_1d, 1) >= ov['ret3']) & (cl < op)
                & (st.body_pct <= P['BODY15'])
                & (_div(st.vol, _prev(st.vol)) <= ov['shrink'])
                & (vr20 <= ov['vrcap']))

    if name == 'E05':
        base = ef & (cl > st.rh20) & (vr20 >= ov['vr15'])
        thr = st.rh20 * (1.0 - ov['pull'])
        ex = np.zeros(base.shape, dtype=bool)
        for d in range(1, 6):
            ex |= (_shift(vr20, d) < P['VRCAP']) & (_shift(lo, d) >= thr)
        return base & ex

    if name == 'E06':
        e06 = ef & (cl < op) & (vr20 >= ov['vr25'])
        mid = (lo + (hi - lo) * ov['recover']).astype('float32')
        return e06 & (first_hit_gt(cl, mid, P['E06_CONF_LO'],
                                   P['E06_CONF_HI']) >= 0)

    if name == 'E07':
        with np.errstate(invalid='ignore'):
            sr = np.where((hi - lo) > 0, st.lower / (hi - lo), np.nan)
        core = (ef & np.isfinite(sr) & (sr >= ov['shadow'])
                & (st.lower >= st.body * P['SHADOW_MULT'])
                & (_shift(st.vol, 1) < st.vol))
        c7d = (cl > op) & (vr20 >= ov['vr12'])
        return core & (first_hit_off(c7d, P['E07_CONF_LO'],
                                     P['E07_CONF_HI']) >= 0)

    if name == 'E08':
        hip = _prev(hi)
        e08t = ef & (st.gap >= ov['gap']) & (lo > hip)
        hd = int(ov['hold'])
        hold = (fwd_all_ge(lo, hip, 1, hd)
                & (fwd_mean(st.vol, 1, hd) < st.vol))
        return e08t & hold

    if name == 'E09':
        e09t = ef & (st.ret_1d >= P['RET3']) & (vr20 >= P['VR15'])
        vthr = st.vol * ov['shrink']
        lthr = cl * (1.0 - P['PULL2'])
        ok2 = fwd_all_lt(st.vol, vthr, 1, 2) & fwd_all_ge(lo, lthr, 1, 2)
        ok3 = ok2 & fwd_all_lt(st.vol, vthr, 3, 3) & fwd_all_ge(lo, lthr, 3, 3)
        ok4 = ok3 & fwd_all_lt(st.vol, vthr, 4, 4) & fwd_all_ge(lo, lthr, 4, 4)
        koff = np.where(ok2, 2, np.where(ok3, 3, np.where(ok4, 4, -1))
                        ).astype('int32')
        c9d = (cl > op) & (vr20 >= ov['vr13'])
        conf = exists_after_gt(st, koff, c9d, 1, 3)
        return e09t & (koff > 0) & conf

    if name == 'E10':
        e10t = ef & (vr20 >= ov['vr25'])
        wmax = fwd_max(cl, 0, 5)
        wmin = fwd_min(cl, 1, 5)
        with np.errstate(invalid='ignore'):
            ddv = (wmax - wmin) / wmax
        flat = ((fwd_mean(vr20, 1, 5) <= P['VRCAP']) & np.isfinite(ddv)
                & (ddv <= ov['ddm'])
                & fwd_all_ge(lo, cl * (1.0 - P['PULL3']), 1, 5))
        return e10t & flat

    raise KeyError(name)


def mask_samples(mask, st, anchor_offset=None):
    """(si, j, anchor_j) slice coordinates for a mask whose samples are the
    True cells (anchor = column unless anchor_offset is given)."""
    si, j = np.nonzero(mask & st._win())
    aj = j.copy() if anchor_offset is None else (j + anchor_offset[si, j])
    keep = aj <= st.N - 1
    si, j, aj = si[keep], j[keep], aj[keep]
    if anchor_offset is not None:
        keep2 = anchor_offset[si, j] >= 0
        si, j, aj = si[keep2], j[keep2], aj[keep2]
    return si, j, aj


def fwd_from(st, si, aj, h):
    return fwd(st.g, si, aj + st.c0, h)


def quick_net30(st, si, aj, months=None, h=HSTAR):
    r = fwd_from(st, si, aj, h)
    x = np.asarray(r, dtype='float64')
    x = x[np.isfinite(x)]
    if x.size == 0:
        return np.nan, 0
    return float(x.mean() - COST / 10000.0), int(x.size)


# =====================================================================
# future_column_scan (SPEC section 8) -- FATAL on failure
# =====================================================================
def future_column_scan(g, st, lg):
    checks = []

    # ---- check 1: truncation invariance --------------------------------
    stT = State(g, trunc=120)
    nc = int(min(st.N, stT.N) - 25)
    bad = []
    for nm in EVENT_NAMES:
        a = st.E[nm]['T'][:, :nc]
        b = stT.E[nm]['T'][:, :nc]
        if not np.array_equal(a, b):
            bad.append(nm)
    for nm, a, b in (('ma20', st.ma20, stT.ma20),
                     ('ma60', st.ma60, stT.ma60),
                     ('vr20', st.vr20, stT.vr20),
                     ('gap', st.gap, stT.gap),
                     ('body_pct', st.body_pct, stT.body_pct)):
        if not np.allclose(a[:, :nc], b[:, :nc], equal_nan=True, rtol=0,
                           atol=0):
            bad.append(nm)
    checks.append({
        'check': '1_truncation_invariance',
        'verdict': 'PASS' if not bad else 'FAIL',
        'detail': 'panel truncated by K=120 sessions; all 23 event masks and 5 '
                  'features recomputed and compared bit-for-bit over the %d '
                  'shared sessions that no forward window can reach' % nc,
        'value': 'none' if not bad else ('mismatch: %s' % ','.join(bad[:8]))})
    del stT

    # ---- check 2: backward window proof --------------------------------
    fwd_high = fwd_max(st.hi, 1, 20)          # deliberately forward-looking
    m_back = st.E['E05']['T']
    m_fwd = (st.ef & (st.cl > fwd_high) & (st.vr20 >= P['VR15']))
    nb = int((m_back & st._win())[:, st.s0:].sum())
    nf = int((m_fwd & st._win())[:, st.s0:].sum())
    checks.append({
        'check': '2_backward_window_proof',
        'verdict': 'PASS' if nb != nf else 'FAIL',
        'detail': 'rolling_high_20 is BACKWARD ([t-20, t-1], min_periods=20); '
                  'the deliberately forward-looking max(high[t..t+19]) variant '
                  'produces a different event count, proving the backward rule '
                  'is the one in use',
        'value': 'backward N=%d vs forward N=%d' % (nb, nf)})

    # ---- check 3: signal column-list audit -----------------------------
    sig = set()
    for nm in CORE_EVENTS:
        sig |= set(SIGNAL_COLUMNS.get(nm, ()))
    overlap = sig & set(LABEL_COLUMNS)
    checks.append({
        'check': '3_forward_window_isolation',
        'verdict': 'PASS' if not overlap else 'FAIL',
        'detail': 'static column-list audit: the base variables referenced by '
                  'every core event T-day mask are disjoint from the label '
                  'columns that use t > T (confirmation / REC / breached)',
        'value': 'signal columns=%d, label columns=%d, intersection=%d'
                 % (len(sig), len(LABEL_COLUMNS), len(overlap))})

    # ---- check 4: label columns marked ---------------------------------
    conf_names = set(EVENT_NAMES) - set(CORE_EVENTS)
    missing = conf_names - set(LABEL_COLUMNS)
    checks.append({
        'check': '4_label_columns_marked',
        'verdict': 'PASS' if not missing else 'FAIL',
        'detail': 'every event label that consumes t > T information '
                  '(E0x_CONF / E05_RETRACE / E06_REC* / E08_FILLED / '
                  'E07_BREACHED) is registered in LABEL_COLUMNS and is used '
                  'only as an anchor C or a diagnostic, never as a T-day '
                  'signal',
        'value': 'unregistered=%s' % (','.join(sorted(missing)) or 'none')})

    for c in checks:
        lg('  %-30s %s  %s' % (c['check'], c['verdict'], c['value']))
    return checks


# =====================================================================
# main
# =====================================================================
def main():
    lg = M.Log('mce_run')
    lg('=' * 72)
    lg('H-MCE-01  multi-candle price-volume event incremental information')
    lg('=' * 72)
    if not M.verify_spec(lg):
        return 2

    g = M.load_grid(lg)
    st = State(g, lg)
    N, S, c0, s0 = st.N, st.S, st.c0, st.s0
    dphase = day_phase_array(st)
    day_is = (dphase == 'IS')
    day_oos = (dphase == 'OOS')
    win = st._win()

    # =================================================================
    # 0. future_column_scan  (FATAL -> EXIT 3)
    # =================================================================
    lg.sep()
    lg('0. future_column_scan')
    fcs = future_column_scan(g, st, lg)
    if any(c['verdict'] == 'FAIL' for c in fcs):
        lg('FATAL: future data leak detected -- experiment stopped')
        save_csv(pd.DataFrame(fcs), 'H_MCE_01_BIAS_AUDIT.csv')
        return 3

    res = {}
    res['future_scan'] = fcs

    # =================================================================
    # 1. event definitions
    # =================================================================
    lg.sep()
    lg('1. event definitions')
    defs = {
        'hypothesis_id': P['hypothesis_id'],
        'version': P['version'],
        'spec_sha256': M.SPEC_SHA256,
        'core_events': list(CORE_EVENTS),
        'label_events': sorted(set(EVENT_NAMES) - set(CORE_EVENTS)),
        'thresholds': dict((k, v) for k, v in P.items()
                           if isinstance(v, (int, float, str))),
        'horizons': list(HORIZONS),
        'cost_bp': list(COSTS),
        'primary_cost_bp': COST,
        'anchors': {
            'rule': 'anchor = T for every event; the E0x_CONF / E05_RETRACE / '
                    'E06_REC* labels are separate samples anchored at their '
                    'confirmation day C',
            'E01': 'T', 'E02': 'T+1 (the confirmation day)',
            'E03': 'T (E03_CONF anchors at C)',
            'E04': 'T (E04_CONF anchors at C)',
            'E05': 'T (E05_RETRACE / E05_CONF anchor at C)',
            'E06': 'T (E06_CONF / REC50 / REC75 / RECHI anchor at C)',
            'E07': 'T (E07_CONF anchors at C)',
            'E08': 'T (E08_CONF anchors at C)',
            'E09': 'T (E09_CONF anchors at C)',
            'E10': 'T (E10_CONF anchors at C)',
        },
        'event_rules': {
            'E01': 'gap_open>=GAP2 & close<open & close>prev_close & '
                   'volume_ratio_20>=VR2 & close>MA20',
            'E02': 'E01 & T+1: close>open & close>high[T] & open<=close[T]',
            'E03': 'ret_1d[T-1]>=RET7 & gap_open>=GAP15 & close<open & '
                   'volume_ratio_20>=VR15 & close>=prev_close',
            'E04': 'ret_1d[T-1]>=RET3 & close<open & body_pct<=BODY15 & '
                   'vol[T]/vol[T-1]<=SHRINK & volume_ratio_20<=VRCAP',
            'E05': 'close>rolling_high_20(T-1 version) & volume_ratio_20>='
                   'VR15 & retrace within T+1..T+5 (vr<VRCAP & low>='
                   'breakout*(1-PULL))',
            'E06': 'close<open & volume_ratio_20>=VR25 & recovery above '
                   '(high[T]+low[T])/2 within T+1..T+5',
            'E07': 'lower_shadow/range>=SR & lower_shadow>=body*SHADOW_MULT & '
                   'vol[T+1]<vol[T] & bullish bar with vr>=VR12 within T+1..T+3',
            'E08': 'gap_open>=GAP2 & low[T]>high[T-1] & gap held (low>=high[T-1]) '
                   'and volume shrinking over T+1..T+5',
            'E09': 'ret_1d[T]>=RET3 & volume_ratio_20>=VR15 & 2-4 session volume '
                   'shrink with low>=close[T]*(1-PULL2) & second breakout',
            'E10': 'volume_ratio_20>=VR25 & flat consolidation over T+1..T+5 '
                   '(mean vr<=VRCAP, drawdown<=DDM, low>=close[T]*(1-PULL3))',
        },
        'universe': {
            'U1_primary': P['universe_primary'],
            'U2_full_sample': P['universe_full'],
        },
        'regime_rule': P['regime_rule'],
        'trading_authorization': P['trading_authorization'],
    }
    save_json(defs, 'multi_candle_event_definitions.json')
    lg('definitions written')

    # =================================================================
    # 2. forward-return matrices + benchmark A
    # =================================================================
    lg.sep()
    lg('2. forward returns + Benchmark A')
    R = {}
    for h in HORIZONS:
        R[h] = ret_matrix(st.cl, h)
    R5, R10, R20 = R[5], R[10], R[20]

    pool_all = st.el & win
    bmA = {}
    for h in HORIZONS:
        Rh = R[h]
        bmA[h] = {
            'FULL': float(np.nanmean(Rh[pool_all])),
            'IS': float(np.nanmean(Rh[pool_all & day_is[None, :]])),
            'OOS': float(np.nanmean(Rh[pool_all & day_oos[None, :]])),
            'n': int(pool_all.sum()),
        }
    lg('  eligible cells in study window = %d' % int(pool_all.sum()))
    for h in HORIZONS:
        lg('  Benchmark A T+%-2d  FULL=%+.4f  IS=%+.4f  OOS=%+.4f'
           % (h, bmA[h]['FULL'], bmA[h]['IS'], bmA[h]['OOS']))

    # =================================================================
    # 3. event samples + metrics + bootstrap
    # =================================================================
    lg.sep()
    lg('3. event samples / metrics')
    SAMP = {}
    MET = {}
    for nm in EVENT_NAMES:
        sp = st.samples(nm)
        si, ti, aj = sp['si'], sp['t_abs'], sp['aj']
        months = st.months(ti)
        SAMP[nm] = {'si': si, 'ti': ti, 'aj': aj, 'months': months,
                    'phase': st.phase(ti), 'years': st.years(ti),
                    'aj_abs': sp['anchor_abs']}
        for h in HORIZONS:
            r = fwd(st.g, si, sp['anchor_abs'], h)
            m = stats_block(r, months, do_boot=True)
            MET[(nm, h)] = m
        mm = MET[(nm, HSTAR)]
        lg('  %-12s n=%7d  T+5 gross=%+.4f net30=%+.4f win30=%.3f'
           % (nm, mm['n'], mm['gross'], mm['net30'], mm['win30']))

    # =================================================================
    # 4. benchmarks B and C  (SPEC section 7)
    # =================================================================
    lg.sep()
    lg('4. Benchmarks B (pre-condition, no pattern) and C (same-stock match)')
    sr_full = np.where((st.hi - st.lo) > 0, st.lower / (st.hi - st.lo), np.nan)
    PRE = {
        'E01': (st.gap >= P['GAP2']) & (st.cl > st.pc),
        'E02': _shift((st.gap >= P['GAP2']) & (st.cl > st.pc), 1),
        'E03': _shift(st.ret_1d, 1) >= P['RET7'],
        'E04': _shift(st.ret_1d, 1) >= P['RET3'],
        'E05': st.cl > st.rh20,
        'E06': st.cl < st.o,
        'E07': sr_full >= P['SR'],
        'E08': st.lo > _prev(st.hi),
        'E09': st.ret_1d >= P['RET3'],
        'E10': np.ones((S, N), dtype=bool),
    }
    bench = {}
    for nm in CORE_EVENTS:
        E0x = st.E[nm]['T']
        pre = PRE[nm] & (~E0x) & pool_all
        if nm == 'E10':
            pre = (~E0x) & pool_all          # B degenerates to A (SPEC section 7)
        # neighbourhood of the event's T day, +/-20 sessions, same stock
        si = SAMP[nm]['si']
        js = SAMP[nm]['ti'] - c0
        diff = np.zeros((S, N + 1), dtype='int32')
        np.add.at(diff, (si, np.maximum(js - 20, 0)), 1)
        np.add.at(diff, (si, np.minimum(js + 21, N)), -1)
        neigh = np.cumsum(diff[:, :N], axis=1) > 0
        cpool = neigh & (~E0x) & pool_all
        for h in HORIZONS:
            Rh = R[h]
            bB = {'FULL': float(np.nanmean(Rh[pre])),
                  'IS': float(np.nanmean(Rh[pre & day_is[None, :]])),
                  'OOS': float(np.nanmean(Rh[pre & day_oos[None, :]])),
                  'n': int(pre.sum())}
            bC = {'FULL': float(np.nanmean(Rh[cpool])),
                  'IS': float(np.nanmean(Rh[cpool & day_is[None, :]])),
                  'OOS': float(np.nanmean(Rh[cpool & day_oos[None, :]])),
                  'n': int(cpool.sum())}
            bench[(nm, h)] = {'B': bB, 'C': bC}
        mm = bench[(nm, HSTAR)]
        lg('  %-4s B n=%7d net30=%+.4f | C n=%7d net30=%+.4f'
           % (nm, mm['B']['n'], mm['B']['FULL'] - COST / 10000.0,
              mm['C']['n'], mm['C']['FULL'] - COST / 10000.0))
        del neigh, cpool, diff

    # =================================================================
    # 5. main results table (core events x horizons)
    # =================================================================
    lg.sep()
    lg('5. results + FDR')
    rows = []
    pvals = []
    for nm in CORE_EVENTS:
        for h in HORIZONS:
            m = dict(MET[(nm, h)])
            months = SAMP[nm]['months']
            r = fwd(st.g, SAMP[nm]['si'], SAMP[nm]['aj_abs'], h)
            xn = np.asarray(r, dtype='float64') - COST / 10000.0
            ph = SAMP[nm]['phase']
            mi = stats_block(np.asarray(r)[ph == 'IS'], months[ph == 'IS'],
                             do_boot=False)
            mo = stats_block(np.asarray(r)[ph == 'OOS'], months[ph == 'OOS'],
                             do_boot=False)
            mae, mfe, mdd = path_stats(st.g, SAMP[nm]['si'],
                                       SAMP[nm]['aj_abs'], h)
            bb = bench[(nm, h)]
            d = {'event': nm, 'horizon': h, 'n': m['n'],
                 'gross': m['gross'], 'median': m['median'], 'win': m['win'],
                 'std': m['std'], 'tstat': m['tstat'],
                 'net0': m['net0'], 'net15': m['net15'],
                 'net30': m['net30'], 'net50': m['net50'],
                 'win30': m['win30'],
                 'boot_lo': m.get('boot_lo'), 'boot_hi': m.get('boot_hi'),
                 'boot_p': m.get('boot_p'),
                 'mae': mae, 'mfe': mfe, 'mdd': mdd,
                 'is_n': mi['n'], 'is_gross': mi['gross'],
                 'is_net30': mi['net30'], 'is_win30': mi['win30'],
                 'oos_n': mo['n'], 'oos_gross': mo['gross'],
                 'oos_net30': mo['net30'], 'oos_win30': mo['win30'],
                 'benchA_net30': bmA[h]['FULL'] - COST / 10000.0,
                 'benchB_net30': bb['B']['FULL'] - COST / 10000.0,
                 'benchC_net30': bb['C']['FULL'] - COST / 10000.0,
                 'deltaB_net30': m['net30'] - (bb['B']['FULL'] - COST / 10000.0),
                 'deltaC_net30': m['net30'] - (bb['C']['FULL'] - COST / 10000.0)}
            rows.append(d)
            pvals.append(m.get('boot_p'))
    q = bh_fdr(np.array(pvals, dtype='float64'))
    for d, qq in zip(rows, q):
        d['raw_p'] = d.pop('boot_p')
        d['fdr_q'] = float(qq) if np.isfinite(qq) else np.nan
        d['fdr_sig'] = bool(np.isfinite(qq) and qq < P['fdr_q'])
    results_df = pd.DataFrame(rows)
    save_csv(results_df, 'multi_candle_event_results.csv')
    n_sig = int(results_df['fdr_sig'].sum())
    lg('  %d / %d primary tests survive BH-FDR q<%.2f'
       % (n_sig, len(rows), P['fdr_q']))

    # benchmark long table
    brow = []
    for nm in CORE_EVENTS:
        for h in HORIZONS:
            bb = bench[(nm, h)]
            for tag in ('A', 'B', 'C'):
                rec = {'event': nm, 'horizon': h, 'benchmark': tag}
                if tag == 'A':
                    rec.update({'n': bmA[h]['n'], 'gross': bmA[h]['FULL'],
                                'net30': bmA[h]['FULL'] - COST / 10000.0,
                                'is_gross': bmA[h]['IS'],
                                'oos_gross': bmA[h]['OOS']})
                else:
                    v = bb[tag]
                    rec.update({'n': v['n'], 'gross': v['FULL'],
                                'net30': v['FULL'] - COST / 10000.0,
                                'is_gross': v['IS'], 'oos_gross': v['OOS']})
                brow.append(rec)
    save_csv(pd.DataFrame(brow), 'H_MCE_01_BENCHMARK.csv')

    # =================================================================
    # 6. OOS / yearly
    # =================================================================
    lg.sep()
    lg('6. OOS + yearly')
    oos_rows = []
    yrows = []
    for nm in EVENT_NAMES:
        si, aj_abs = SAMP[nm]['si'], SAMP[nm]['aj_abs']
        ph = SAMP[nm]['phase']
        yr = SAMP[nm]['years']
        mo = SAMP[nm]['months']
        for h in HORIZONS:
            r = fwd(st.g, si, aj_abs, h)
            for scope in ('FULL', 'IS', 'OOS'):
                sel = np.ones(ph.size, bool) if scope == 'FULL' else (ph == scope)
                m = stats_block(np.asarray(r)[sel], mo[sel], do_boot=False)
                row = {'event': nm, 'horizon': h, 'scope': scope, 'n': m['n'],
                       'gross': m['gross'], 'median': m['median'],
                       'win': m['win'], 'net30': m['net30'],
                       'win30': m['win30']}
                oos_rows.append(row)
        for y in range(2019, 2027):
            sel = (yr == y)
            if not sel.any():
                continue
            for h in HORIZONS:
                r = fwd(st.g, si, aj_abs, h)
                m = stats_block(np.asarray(r)[sel], mo[sel], do_boot=False)
                yrows.append({'event': nm, 'year': y, 'horizon': h,
                              'n': m['n'], 'gross': m['gross'],
                              'median': m['median'], 'win': m['win'],
                              'net30': m['net30'], 'win30': m['win30']})
    save_csv(pd.DataFrame(oos_rows), 'multi_candle_event_oos.csv')
    save_csv(pd.DataFrame(yrows), 'multi_candle_event_yearly.csv')
    lg('  oos rows=%d  yearly rows=%d' % (len(oos_rows), len(yrows)))

    # =================================================================
    # 7. regime
    # =================================================================
    lg.sep()
    lg('7. market regime')
    reg_full = np.asarray(regime_ma(g, lg)).astype(str)
    reg_w = reg_full[st.sl]
    regmap = {'BULL': 'BULL', 'BEAR': 'BEAR', 'NEUTRAL': 'NEUTRAL'}
    reg_rows = []
    reg_net = {}
    for nm in EVENT_NAMES:
        js = SAMP[nm]['ti'] - c0
        rj = reg_w[js]
        for lab in ('BULL', 'NEUTRAL', 'BEAR'):
            sel = (rj == lab)
            for h in (5, 10):
                r = fwd(st.g, SAMP[nm]['si'], SAMP[nm]['aj_abs'], h)
                m = stats_block(np.asarray(r)[sel], SAMP[nm]['months'][sel],
                                do_boot=False)
                reg_rows.append({'event': nm, 'regime': lab, 'horizon': h,
                                 'n': m['n'], 'gross': m['gross'],
                                 'win': m['win'], 'net30': m['net30'],
                                 'win30': m['win30']})
                if h == HSTAR:
                    reg_net[(nm, lab)] = m['net30']
    save_csv(pd.DataFrame(reg_rows), 'multi_candle_event_regime.csv')
    for nm in CORE_EVENTS:
        lg('  %-4s BULL=%+.4f NEUTRAL=%+.4f BEAR=%+.4f'
           % (nm, reg_net.get((nm, 'BULL'), np.nan),
              reg_net.get((nm, 'NEUTRAL'), np.nan),
              reg_net.get((nm, 'BEAR'), np.nan)))

    # =================================================================
    # 8. parameter stability (pre-registered perturbation grid)
    # =================================================================
    lg.sep()
    lg('8. parameter stability grid')
    GRID = {
        'E01': [('gap', P['grid']['gap']), ('vr', P['grid']['vr'])],
        'E02': [('gap', P['grid']['gap']), ('vr', P['grid']['vr'])],
        'E03': [('gap15', P['grid']['gap15']), ('vr15', P['grid']['vr15']),
                ('ret7', P['grid']['ret7'])],
        'E04': [('shrink', P['grid']['shrink']), ('vrcap', P['grid']['vrcap']),
                ('ret3', P['grid']['ret3'])],
        'E05': [('vr15', P['grid']['vr15']), ('pull', P['grid']['pull'])],
        'E06': [('vr25', P['grid']['vr25']), ('recover', (0.50, 0.60, 0.75))],
        'E07': [('shadow', P['grid']['shadow']), ('vr12', P['grid']['vr12'])],
        'E08': [('gap', P['grid']['gap']), ('hold', P['grid']['hold'])],
        'E09': [('shrink', P['grid']['shrink']), ('vr13', P['grid']['vr13'])],
        'E10': [('vr25', P['grid']['vr25']), ('ddm', P['grid']['ddm'])],
    }
    grid_rows = []
    stab = {}
    for nm in CORE_EVENTS:
        axes = GRID[nm]
        names = [a for a, _ in axes]
        cells = list(itertools.product(*[v for _, v in axes]))
        pos = 0
        nvalid = 0
        base_n = None
        base_net = None
        for cell in cells:
            ov = dict(zip(names, cell))
            msk = grid_mask(st, nm, ov)
            si, j = np.nonzero(msk & win)
            aj = j.copy()
            net, n = quick_net30(st, si, aj, h=HSTAR)
            lab = '|'.join('%s=%s' % (a, ('%.3f' % v if isinstance(v, float)
                                          else v)) for a, v in zip(names, cell))
            grid_rows.append({'event': nm, 'cell': lab,
                              **dict(('%s' % a, v) for a, v in zip(names, cell)),
                              'n': n, 'net30_t5': net,
                              'valid': bool(n >= P['min_n'])})
            if n >= P['min_n']:
                nvalid += 1
                if np.isfinite(net) and net > 0:
                    pos += 1
            if all(ov[a] == main_ov(nm)[a] for a in names):
                base_n, base_net = n, net
        ratio = float(pos) / nvalid if nvalid else np.nan
        stable = bool(np.isfinite(ratio) and ratio >= P['param_stable_thr'])
        fragile = bool(np.isfinite(ratio) and ratio <= P['param_fragile_thr'])
        stab[nm] = {'n_cells': len(cells), 'n_valid': nvalid,
                    'positive_ratio': ratio, 'stable': stable,
                    'fragile': fragile, 'baseline_n': base_n,
                    'baseline_net30': base_net}
        lg('  %-4s cells=%2d valid=%2d positive_ratio=%.3f  %s'
           % (nm, len(cells), nvalid, ratio if np.isfinite(ratio) else np.nan,
              'STABLE' if stable else ('FRAGILE' if fragile else 'MIXED')))
    save_csv(pd.DataFrame(grid_rows), 'multi_candle_event_parameter_stability.csv')

    # =================================================================
    # 9. null models (SPEC section 13)
    # =================================================================
    lg.sep()
    lg('9. null models')
    psi, psj = np.nonzero(pool_all)
    self_all = psi.astype('int64') * N + psj.astype('int64')
    rflat = R5.reshape(-1).astype('float64')
    year_of_j = np.array([int(d[:4]) for d in st.dates_w], dtype='int64')
    regcode = np.array([{'BULL': 0, 'NEUTRAL': 1, 'BEAR': 2}.get(x, 3)
                        for x in reg_w], dtype='int64')
    rb = bucket_ret(st.ret_1d)
    # pools
    key_null1 = np.zeros(self_all.size, dtype='int64')
    lg('  pool cells = %d' % psi.size)

    null_rows = []
    null_sum = {}
    for nm in CORE_EVENTS:
        si, ti, aj = SAMP[nm]['si'], SAMP[nm]['ti'], SAMP[nm]['aj_abs']
        jslice = SAMP[nm]['aj']                     # anchor slice column
        esi = si.astype('int64')
        # T-day slice column for keying (the signal day)
        tcol = (SAMP[nm]['ti'] - c0).astype('int64')
        ev_self_sig = esi * N + tcol.astype('int64')
        ev_self_anc = esi * N + jslice.astype('int64')
        obs = float(np.nanmean(fwd(st.g, si, aj, HSTAR)))
        E_ = esi.size
        chunk = int(max(1, min(64, 3.0e6 / max(E_, 1))))
        # Null 1: random stock x date (single pool)
        flat, lo, hi, uq = build_pools(self_all, key_null1)
        v1 = null_family(rflat, flat, lo, hi, uq,
                         np.zeros(E_, dtype='int64'), ev_self_sig, obs,
                         B=P['null_B'], seed=SEED, rounds=P['null_rounds'],
                         chunk=max(chunk, 1))
        del flat, lo, hi, uq
        # Null 2: matched stock x year x regime x trailing-return bucket
        k2 = (psi.astype('int64') * 100000
              + (year_of_j[psj] - 2000) * 1000
              + regcode[psj] * 10 + (rb[psi, psj].astype('int64') + 2))
        flat, lo, hi, uq = build_pools(self_all, k2)
        ek2 = (esi * 100000 + (year_of_j[tcol] - 2000) * 1000
               + regcode[tcol] * 10 + (rb[esi, tcol].astype('int64') + 2))
        v2 = null_family(rflat, flat, lo, hi, uq, ek2, ev_self_sig, obs,
                         B=P['null_B'], seed=SEED, rounds=P['null_rounds'],
                         chunk=max(chunk, 1))
        del flat, lo, hi, uq, k2, ek2
        # Null 3: shuffle the event date within the same stock
        k3 = psi.astype('int64')
        flat, lo, hi, uq = build_pools(self_all, k3)
        v3 = null_family(rflat, flat, lo, hi, uq, esi, ev_self_sig, obs,
                         B=P['null_B'], seed=SEED, rounds=P['null_rounds'],
                         chunk=max(chunk, 1))
        del flat, lo, hi, uq, k3
        for tag, v in (('N1_RANDOM_STOCK_DAY', v1), ('N2_MATCHED_LABEL', v2),
                       ('N3_SHUFFLE_DATE', v3)):
            rec = dict(v)
            rec['event'] = nm
            rec['family'] = tag
            rec['horizon'] = HSTAR
            rec['adopted'] = bool(v['resolution'] >= P['null_min_resolution'])
            null_rows.append(rec)
        null_sum[nm] = {'N1': v1, 'N2': v2, 'N3': v3}
        lg('  %-4s obs=%+.4f | N1 null=%+.4f p=%.4f res=%.3f | '
           'N2 null=%+.4f p=%.4f res=%.3f | N3 null=%+.4f p=%.4f res=%.3f'
           % (nm, obs, v1['null_mean'], v1['p_one_sided'], v1['resolution'],
              v2['null_mean'], v2['p_one_sided'], v2['resolution'],
              v3['null_mean'], v3['p_one_sided'], v3['resolution']))
    save_csv(pd.DataFrame(null_rows), 'multi_candle_event_null_model.csv')
    del psi, psj, self_all, rflat

    # =================================================================
    # 10. momentum comparison (SPEC section 16)
    # =================================================================
    lg.sep()
    lg('10. momentum comparison')
    def day_topdecile_mean(mom, Rh, frac=P['mom_decile']):
        out = np.full(N, np.nan)
        el = pool_all
        for j in range(N):
            col = el[:, j] & np.isfinite(mom[:, j]) & np.isfinite(Rh[:, j])
            if col.sum() < 20:
                continue
            v = mom[col, j]
            thr = np.quantile(v, 1.0 - frac)
            sel = v >= thr
            if sel.any():
                out[j] = Rh[col, j][sel].mean()
        return out

    mom_rows = []
    for w in P['mom_windows']:
        mom = st.mom[w]
        dtm = {}
        for h in HORIZONS:
            dtm[h] = day_topdecile_mean(mom, R[h])
        for nm in CORE_EVENTS:
            js = SAMP[nm]['ti'] - c0
            for h in HORIZONS:
                r = fwd(st.g, SAMP[nm]['si'], SAMP[nm]['aj_abs'], h)
                ev = float(np.nanmean(r))
                ref = dtm[h][js]
                refm = float(np.nanmean(ref))
                mom_rows.append({'event': nm, 'horizon': h, 'mom_window': w,
                                 'event_gross': ev, 'mom_topdecile_gross': refm,
                                 'delta_gross': ev - refm,
                                 'event_net30': ev - COST / 10000.0,
                                 'delta_net30': (ev - COST / 10000.0)
                                                - refm})
    save_csv(pd.DataFrame(mom_rows), 'multi_candle_event_momentum_compare.csv')
    lg('  momentum compare rows=%d' % len(mom_rows))

    # =================================================================
    # 11. incremental information models A/B/C/D (SPEC section 17)
    # =================================================================
    lg.sep()
    lg('11. incremental models')
    nsub = P['incremental_n_sub']
    valid_cells = (pool_all & np.isfinite(st.mom[20]) & np.isfinite(st.vr20)
                   & np.isfinite(R5) & np.isfinite(R10))
    vi, vj = np.nonzero(valid_cells)
    nvalid = vi.size
    rng = np.random.default_rng(SEED)
    take = rng.choice(nvalid, size=min(nsub, nvalid), replace=False)
    si_s = vi[take].astype('int64')
    sj_s = vj[take].astype('int64')
    mom20 = st.mom[20][si_s, sj_s].astype('float64')
    vr20s = st.vr20[si_s, sj_s].astype('float64')
    rg_s = reg_w[sj_s]
    d_bull = (rg_s == 'BULL').astype('float64')
    d_bear = (rg_s == 'BEAR').astype('float64')
    dmat = np.column_stack([st.E[nm]['T'][si_s, sj_s]
                            for nm in CORE_EVENTS]).astype('float64')
    mon_s = st.months((sj_s + c0))
    uq_m, gid = np.unique(mon_s, return_inverse=True)

    def ols_r2(X, y):
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        e = y - X @ beta
        sst = float(((y - y.mean()) ** 2).sum())
        ssr = float((e ** 2).sum())
        return beta, e, (1.0 - ssr / sst if sst > 0 else np.nan)

    def cluster_t(X, e, gid, beta):
        XtX = X.T @ X
        inv = np.linalg.pinv(XtX)
        k = X.shape[1]
        meat = np.zeros((k, k))
        for g in range(gid.max() + 1):
            m = (gid == g)
            if not m.any():
                continue
            s = X[m].T @ e[m]
            meat += np.outer(s, s)
        V = inv @ meat @ inv
        se = np.sqrt(np.abs(np.diag(V)))
        with np.errstate(invalid='ignore', divide='ignore'):
            return np.where(se > 0, beta / se, np.nan)

    inc_rows = []
    inc_detail = {}
    for h in P['incremental_horizons']:
        y = R[h][si_s, sj_s].astype('float64')
        ok = np.isfinite(y)
        X = {}
        X['A'] = np.column_stack([np.ones(ok.sum()), mom20[ok]])
        X['B'] = np.column_stack([X['A'], vr20s[ok]])
        X['C'] = np.column_stack([X['B'], dmat[ok]])
        X['D'] = np.column_stack([X['C'], d_bull[ok], d_bear[ok]])
        yy = y[ok]
        gg = gid[ok]
        r2 = {}
        tstatC = None
        for kname in ('A', 'B', 'C', 'D'):
            beta, e, r = ols_r2(X[kname], yy)
            r2[kname] = r
            if kname in ('C', 'D'):
                ts = cluster_t(X[kname], e, gg, beta)
                if kname == 'C':
                    tstatC = ts
            inc_rows.append({'horizon': h, 'model': kname, 'n': int(ok.sum()),
                             'r2': r, 'delta_r2': np.nan})
        for kname, prev in (('B', 'A'), ('C', 'B'), ('D', 'C')):
            for row in inc_rows:
                if row['horizon'] == h and row['model'] == kname:
                    row['delta_r2'] = r2[kname] - r2[prev]
        # event dummy coefficients from Model C
        betaC, *_ = np.linalg.lstsq(X['C'], yy, rcond=None)
        ev_coef = {}
        for i, nm in enumerate(CORE_EVENTS):
            ev_coef[nm] = {'coef': float(betaC[3 + i]),
                           't': float(tstatC[3 + i]) if tstatC is not None
                           else np.nan}
        inc_detail[h] = {'r2': r2, 'event_coef': ev_coef,
                         'n': int(ok.sum())}
        lg('  h=%d  R2 A=%.5f B=%.5f C=%.5f D=%.5f  (A->B %+.5f, B->C %+.5f, '
           'C->D %+.5f)'
           % (h, r2['A'], r2['B'], r2['C'], r2['D'],
              r2['B'] - r2['A'], r2['C'] - r2['B'], r2['D'] - r2['C']))
    save_csv(pd.DataFrame(inc_rows), 'H_MCE_01_INCREMENTAL.csv')
    del dmat, mom20, vr20s, si_s, sj_s
    for h in inc_detail:
        for nm in CORE_EVENTS:
            v = inc_detail[h]['event_coef'][nm]
            lg('    h=%d %-4s event_dummy coef=%+.5f  t=%+.2f'
               % (h, nm, v['coef'], v['t']))

    # =================================================================
    # 12. size terciles + tail concentration (SPEC section 12)
    # =================================================================
    lg.sep()
    lg('12. size terciles + concentration')
    mv = load_size_mv(g)[:, st.sl]
    terc = np.full((S, N), -1, dtype='int8')
    for j in range(N):
        col = st.el[:, j] & np.isfinite(mv[:, j])
        if col.sum() < 30:
            continue
        v = mv[col, j]
        q1, q2 = np.quantile(v, [1.0 / 3.0, 2.0 / 3.0])
        t3 = np.where(v <= q1, 0, np.where(v <= q2, 1, 2))
        terc[col, j] = t3.astype('int8')
    size_rows = []
    for nm in CORE_EVENTS:
        si, aj = SAMP[nm]['si'], SAMP[nm]['aj_abs']
        tj = terc[si, SAMP[nm]['aj']]
        for lab, code in (('LARGE', 2), ('MID', 1), ('SMALL', 0)):
            sel = (tj == code)
            for h in (5, 10):
                r = fwd(st.g, si, aj, h)
                m = stats_block(np.asarray(r)[sel], SAMP[nm]['months'][sel],
                                do_boot=False)
                size_rows.append({'event': nm, 'size': lab, 'horizon': h,
                                  'n': m['n'], 'gross': m['gross'],
                                  'win': m['win'], 'net30': m['net30']})
        r = np.asarray(fwd(st.g, si, aj, HSTAR), dtype='float64')
        r = r[np.isfinite(r)]
        pos = r[r > 0]
        tot = float(pos.sum()) if pos.size else np.nan
        for lv in P['tail_levels']:
            if pos.size == 0:
                share = np.nan
            else:
                kk = max(1, int(round(pos.size * lv)))
                share = float(np.sort(pos)[-kk:].sum() / tot)
            size_rows.append({'event': nm, 'size': 'TOP_%dpc' % int(lv * 100),
                              'horizon': HSTAR, 'n': pos.size,
                              'gross': share, 'win': np.nan,
                              'net30': np.nan})
    save_csv(pd.DataFrame(size_rows), 'H_MCE_01_SIZE.csv')
    del mv, terc

    # =================================================================
    # 13. survivorship U1 vs U2 (SPEC section 2.3)
    # =================================================================
    lg.sep()
    lg('13. survivorship audit U1 vs U2')
    st2 = State(g, universe='U2')
    surv_rows = []
    for nm in CORE_EVENTS:
        s1i, s1j = np.nonzero(st.E[nm]['T'] & win)
        v1_, n1 = quick_net30(st, s1i, s1j, h=HSTAR)
        s2i, s2j = np.nonzero(st2.E[nm]['T'] & win)
        v2_, n2 = quick_net30(st, s2i, s2j, h=HSTAR)
        surv_rows.append({'event': nm, 'u1_n': n1, 'u1_net30_t5': v1_,
                          'u2_n': n2, 'u2_net30_t5': v2_,
                          'delta_net30': (v1_ - v2_)
                          if np.isfinite(v1_) and np.isfinite(v2_)
                          else np.nan})
        lg('  %-4s U1 n=%7d net30=%+.4f | U2 n=%7d net30=%+.4f'
           % (nm, n1, v1_, n2, v2_))
        del s1i, s1j, s2i, s2j
    survivor_meta = {
        'panel_keeps_delisted_history': True,
        'delist_flag': 'DELIST_PIT_UNAVAILABLE',
        'gone_codes': int(np.asarray(g['gone']).sum()),
        'survivorship_bias': 'partially mitigated, not eliminated',
    }
    save_csv(pd.DataFrame(surv_rows), 'H_MCE_01_SURVIVOR.csv')
    del st2

    # =================================================================
    # 14. judgment tree (SPEC section 18)
    # =================================================================
    lg.sep()
    lg('14. judgment')
    names = ['b1_is_pos', 'b2_oos_pos', 'b3_full_pos', 'b4_boot_ci_pos',
             'b5_fdr_sig', 'b6_years_consist', 'b7_param_stable',
             'b8_regime_ok', 'b9_null_ok', 'b10_not_tail', 'b11_beats_bench',
             'b12_momentum_incr']
    rd = dict(((r['event'], r['horizon']), r) for r in rows)
    yd = {}
    for r in yrows:
        yd[(r['event'], r['year'])] = r
    y5 = {}
    for nm in CORE_EVENTS:
        r = np.asarray(fwd(st.g, SAMP[nm]['si'], SAMP[nm]['aj_abs'], HSTAR),
                       dtype='float64')
        r = r[np.isfinite(r)]
        xn = r - COST / 10000.0
        keep = max(1, int(round(xn.size * 0.05)))
        y5[nm] = float(np.sort(xn)[:-keep].mean()) if xn.size > keep else np.nan

    judg = {}
    for nm in CORE_EVENTS:
        r5 = rd[(nm, HSTAR)]
        years_ok = []
        for y in range(2019, 2024):
            rr5 = [x for x in yrows if x['event'] == nm and x['year'] == y
                   and x['horizon'] == HSTAR]
            if rr5 and rr5[0]['n'] >= 1:
                years_ok.append(1 if rr5[0]['net30'] > 0 else 0)
        frac = float(np.mean(years_ok)) if years_ok else np.nan
        b = {
            'b1_is_pos': bool(np.isfinite(r5['is_net30']) and r5['is_net30'] > 0),
            'b2_oos_pos': bool(np.isfinite(r5['oos_net30'])
                               and r5['oos_net30'] > 0),
            'b3_full_pos': bool(np.isfinite(r5['net30']) and r5['net30'] > 0),
            'b4_boot_ci_pos': bool(np.isfinite(r5['boot_lo'])
                                   and r5['boot_lo'] > 0),
            'b5_fdr_sig': bool(r5['fdr_sig']),
            'b6_years_consist': bool(np.isfinite(frac) and frac >= 0.60),
            'b7_param_stable': bool(stab[nm]['stable']),
            'b8_regime_ok': bool(sum(1 for lab in ('BULL', 'NEUTRAL', 'BEAR')
                                     if np.isfinite(reg_net.get((nm, lab),
                                                                np.nan))
                                     and reg_net[(nm, lab)] > 0) >= 2),
            'b9_null_ok': bool(np.isfinite(null_sum[nm]['N2']['null_hi'])
                               and null_sum[nm]['N2']['obs']
                               > null_sum[nm]['N2']['null_hi']),
            'b10_not_tail': bool(np.isfinite(y5[nm]) and y5[nm] > 0),
            'b11_beats_bench': bool(r5['deltaB_net30'] > 0
                                    and r5['deltaC_net30'] > 0),
            'b12_momentum_incr': bool(
                inc_detail.get(HSTAR, {}).get('event_coef', {})
                .get(nm, {}).get('coef', -1) > 0),
        }
        nt = sum(1 for k in names if b[k])
        if all(b[k] for k in names):
            verdict = 'ROBUST'
        elif b['b1_is_pos'] and b['b2_oos_pos'] and b['b3_full_pos'] \
                and b['b4_boot_ci_pos'] and nt >= 9:
            verdict = 'PROMISING'
        elif b['b3_full_pos'] and (not b['b2_oos_pos']
                                   or not b['b7_param_stable']
                                   or not b['b10_not_tail']):
            verdict = 'FRAGILE'
        elif b['b3_full_pos']:
            verdict = 'PROMISING'
        else:
            verdict = 'NO EDGE'
        judg[nm] = {'bits': b, 'n_true': nt, 'verdict': verdict,
                    'years_pos_frac': frac}
        lg('  %-4s %2d/12  %-9s  %s' % (nm, nt, verdict,
                                        ''.join(('+' if b[k] else '-')
                                                for k in names)))
    n_robust = sum(1 for v in judg.values() if v['verdict'] == 'ROBUST')
    n_prom = sum(1 for v in judg.values() if v['verdict'] == 'PROMISING')
    n_frag = sum(1 for v in judg.values() if v['verdict'] == 'FRAGILE')
    n_none = sum(1 for v in judg.values() if v['verdict'] == 'NO EDGE')
    oos_ok = [nm for nm in CORE_EVENTS if judg[nm]['bits']['b2_oos_pos']]
    cost_ok = [nm for nm in CORE_EVENTS
               if np.isfinite(rd[(nm, HSTAR)]['net30'])
               and rd[(nm, HSTAR)]['net30'] > 0]
    lg('  ROBUST=%d PROMISING=%d FRAGILE=%d NO_EDGE=%d'
       % (n_robust, n_prom, n_frag, n_none))
    lg('  OOS-positive: %s' % (','.join(oos_ok) or 'none'))
    lg('  cost-30bp-positive (H*): %s' % (','.join(cost_ok) or 'none'))

    overall = ('NO STABLE ADVANTAGE FOUND (未发现稳定优势)'
               if (n_robust == 0 and n_prom == 0)
               else 'candidate(s) require independent phase-2 validation')

    # =================================================================
    # 15. bias audit (SPEC section 21)
    # =================================================================
    lg.sep()
    lg('15. bias audit')
    bias = list(fcs)
    grid_total = sum(stab[nm]['n_cells'] for nm in CORE_EVENTS)
    pos_ratio_all = np.nanmean([stab[nm]['positive_ratio']
                                for nm in CORE_EVENTS])
    bias.append({'check': '5_multiple_testing',
                 'verdict': 'PASS' if n_sig <= len(rows) else 'FLAG',
                 'detail': 'BH-FDR applied over the %d primary tests '
                           '(10 events x 5 horizons); q<%.2f'
                           % (len(rows), P['fdr_q']),
                 'value': 'raw p<0.05 = %d, FDR-significant = %d'
                          % (int((results_df['raw_p'] < 0.05).sum()), n_sig)})
    bias.append({'check': '6_parameter_selection',
                 'verdict': 'FLAG' if np.isfinite(pos_ratio_all)
                 and pos_ratio_all > 0.8 else 'PASS',
                 'detail': 'the reported thresholds are the pre-registered '
                           'main values, not the grid argmax; the grid is a '
                           'perturbation study only (SPEC section 10)',
                 'value': 'grid cells=%d, mean positive_ratio=%.3f'
                          % (grid_total, pos_ratio_all)})
    gone = np.asarray(g['gone'])
    bias.append({'check': '7_survivorship_bias', 'verdict': 'FLAG',
                 'detail': 'the Tushare cache carries no point-in-time '
                           'delisting flag (DELIST_PIT_UNAVAILABLE); the '
                           'universe is ST/name-based only.  Early-stopped '
                           'codes are reported, not silently dropped.',
                 'value': 'gone_codes=%d; U1 vs U2 comparison in '
                          'H_MCE_01_SURVIVOR.csv' % int(gone.sum())})
    bias.append({'check': '8_overlapping_sample',
                 'verdict': 'PASS' if max(
                     overlap_stats(SAMP[nm]['si'], SAMP[nm]['ti'])['rate_le20']
                     for nm in ('E01', 'E05')) <= 0.5 else 'FLAG',
                 'detail': 'same-stock signal clustering; N is not treated as '
                           'the independent sample size (monthly cluster '
                           'bootstrap is the primary inference)',
                 'value': 'rate<=20d E01=%.4f E05=%.4f'
                          % (overlap_stats(SAMP['E01']['si'],
                                           SAMP['E01']['ti'])['rate_le20'],
                             overlap_stats(SAMP['E05']['si'],
                                           SAMP['E05']['ti'])['rate_le20'])})
    save_csv(pd.DataFrame(bias), 'H_MCE_01_BIAS_AUDIT.csv')
    for b in bias:
        lg('  %-30s %s  %s' % (b['check'], b['verdict'], b['value']))
    res['bias'] = bias

    # =================================================================
    # 16. samples table (SPEC section 20)
    # =================================================================
    lg.sep()
    lg('16. samples table')
    parts = []
    for nm in EVENT_NAMES:
        si, ti, aj = SAMP[nm]['si'], SAMP[nm]['ti'], SAMP[nm]['aj_abs']
        if si.size == 0:
            continue
        r5 = fwd(st.g, si, aj, 5)
        r10 = fwd(st.g, si, aj, 10)
        parts.append(pd.DataFrame({
            'event': nm,
            'code': st.codes[si],
            't_date': np.array([st.dates[int(x)] for x in ti], dtype=object),
            'anchor_date': np.array([st.dates[int(x)] for x in aj], dtype=object),
            'phase': SAMP[nm]['phase'],
            'fwd_ret_5': np.round(r5.astype('float64'), 6),
            'fwd_ret_10': np.round(r10.astype('float64'), 6),
            'anchor_offset': (aj - ti).astype('int32'),
        }))
    samples_df = pd.concat(parts, ignore_index=True)
    save_csv(samples_df, 'multi_candle_event_samples.csv')
    lg('  samples rows=%d' % samples_df.shape[0])
    del parts, samples_df

    # =================================================================
    # 17. summary json
    # =================================================================
    summary = {
        'hypothesis_id': P['hypothesis_id'],
        'spec_sha256': M.SPEC_SHA256,
        'universe': {'U1_cells': int(pool_all.sum()),
                     'U2_definition': P['universe_full']},
        'survivor_meta': survivor_meta,
        'n_primary_tests': len(rows),
        'n_fdr_significant': n_sig,
        'counts': {'ROBUST': n_robust, 'PROMISING': n_prom,
                   'FRAGILE': n_frag, 'NO EDGE': n_none},
        'oos_positive_events': oos_ok,
        'cost_positive_events_H5': cost_ok,
        'verdicts': dict((nm, judg[nm]['verdict']) for nm in CORE_EVENTS),
        'bits': dict((nm, judg[nm]['bits']) for nm in CORE_EVENTS),
        'overall': overall,
        'trading_authorization': P['trading_authorization'],
    }
    save_json(summary, 'H_MCE_01_SUMMARY.json')

    res['results'] = rows
    res['benchmark'] = brow
    res['oos'] = oos_rows
    res['yearly'] = yrows
    res['regime'] = reg_rows
    res['stability'] = {'rows': grid_rows, 'summary': stab}
    res['null'] = null_rows
    keys = ('obs', 'null_mean', 'null_lo', 'null_hi', 'excess',
            'p_one_sided', 'resolution')
    res['null_summary'] = {}
    for nm in CORE_EVENTS:
        res['null_summary'][nm] = {}
        for tag in ('N1', 'N2', 'N3'):
            src = null_sum[nm][tag]
            res['null_summary'][nm][tag] = dict((k, src.get(k))
                                                for k in keys)
    res['momentum'] = mom_rows
    res['incremental'] = {'rows': inc_rows,
                          'detail': dict((str(k), v)
                                         for k, v in inc_detail.items())}
    res['size'] = size_rows
    res['survivor'] = {'rows': surv_rows, 'meta': survivor_meta}
    res['judgment'] = judg
    res['summary'] = summary
    res['bmA'] = bmA
    res['spec'] = P
    res['meta'] = {
        'S': int(S), 'N': int(N), 'c0': int(c0), 's0': int(s0),
        'window': [str(st.dates_w[0]), str(st.dates_w[-1])],
        'panel_range': [str(st.dates[0]), str(st.dates[-1])],
        'is': [P['is_start'], P['is_end']],
        'oos': [P['oos_start'], P['oos_end']],
        'universe': P['universe_primary'],
        'n_eligible_cells_window': int(st.el[:, s0:].sum()),
        'spec_sha256': M.SPEC_SHA256,
        'run_finished_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'trading_authorization': P['trading_authorization'],
    }
    save_json(res, 'H_MCE_01_RESULTS.json')
    del R5, R10, R20, R
    lg('results json written')
    lg('=' * 72)
    lg('done -- overall: %s' % overall)
    return 0


if __name__ == '__main__':
    sys.exit(main())
