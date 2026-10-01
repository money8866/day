# -*- coding: utf-8 -*-
"""hve_w7 -- spec sections 13 / 14 / 15 / 28 (and the W7 half of 16).

Deliverable
    04_w7_breakout_analysis.csv   the W7 chain and the four breakout
                                  definitions (sections 13 / 14)
    05_w7_entry_comparison.csv    breakout-day vs pre-breakout entries
                                  (sections 15 / 28)
    plus the matching summary JSONs.

    第13章  W7 is the chain
                HVE -> Price Digestion -> Volume Contraction ->
                Structure Preservation -> Second Expansion -> Breakout
            Each link is made explicit and the chain is measured one link at
            a time (W0 HVE, W1 digestion, W2 contraction, W3 structure) so
            the marginal contribution of every link is visible.
    第14章  four breakout definitions -- B20 / B40 / B_HVE / B_LOCAL --
            are measured side by side.  None of them is designated best.
    第15章  three entries: E1 before the breakout, E2 at the breakout close,
            E3 on the pullback that holds.  Compared at T+3/5/10/20 with MFE
            and MAE.
    第28章  the same question with the six registered entry variants
            (Entry-5 / -3 / -1 / breakout day / breakout+1 / pullback).

Discipline
    * Everything is evaluated with information up to the session being
      tested.  The breakout search never looks at a later session; the
      contraction must have COMPLETED strictly before the breakout.
    * E1 (before the breakout) is reported, as the specification demands,
      but it is flagged: the event set is defined by a breakout that has not
      happened yet at the entry session, so an E1 row is a conditional
      comparison, not a tradable specification.  The `selection` column
      carries that on every row.  Section 28 asks which entry is most stable
      OOS -- not which has the highest mean -- and the tables are read that
      way.
    * No composite score, no weights, no "best parameter".
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hve_common import (PREREG, Log, build_grid, build_indicators,    # noqa
                        save_csv, save_json, DATA, Q_IS, Q_VALID, Q_OOS,
                        _roll_mean, _roll_max_prev, fwd_ret, win_post,
                        contraction_hit, ledger_block, mfe_mae_at)     # noqa

lg = Log('hve_w7')

EV_IN = os.path.join(DATA, 'hve_path_events.parquet')
W = PREREG['w7_confirm_max_wait']          # 60: breakout search window
PW = PREREG['entry_pullback_max_wait']     # 10: pullback wait
KW = W + PW + 2                            # window used for the gathers
HORIZONS = PREREG['horizons']              # (3, 5, 10, 20)
DEFS = PREREG['breakout_defs']
LOCAL_W = PREREG['breakout_local_window']
VOL_MIN = PREREG['breakout_vol_min']
TOL = PREREG['entry_pullback_tol']
DIGEST_MIN = PREREG['w7_digest_min_days']
CT_THR, CT_D = PREREG['contraction_primary']
TAIL_H = PREREG['mfe_mae_window']

ENTRIES = PREREG['entry_variants']
# how many sessions before the breakout each fixed-offset entry books at
PRE_LAG = {'ENTRY_PRE5': 5, 'ENTRY_PRE3': 3, 'ENTRY_PRE1': 1, 'ENTRY_BD': 0,
           'ENTRY_BD1': -1}
SEL_LOOK = 'entry_precedes_breakout_selection'
SEL_CLEAN = 'none'

block = ledger_block


def win(M, si, di, K=KW):
    return win_post(M, si, di, K)


def _shift1(M, fill):
    """M shifted one column to the right: column j reads the value at j-1."""
    out = np.empty_like(M)
    out[:, 0] = fill
    out[:, 1:] = M[:, :-1]
    return out


def _first(cond, ncol):
    """First 1-based column index < ncol where cond is True, else 0."""
    c = cond[:, :ncol]
    hit = c.any(axis=1)
    j = np.argmax(c, axis=1) + 1
    return np.where(hit, j, 0).astype('int32'), hit


def build_w7(g, ind, px, si, di):
    """Every W7 condition, per event, per session of t0+1 .. t0+KW.

    Returns a dict of (E, KW) arrays.  Nothing here reads a session later
    than the column it is written to.
    """
    E = len(si)
    cl, okw = win(g['close'], si, di)
    vr = win(ind['VOL_MA20'], si, di)[0]        # vol / MA20(vol)
    ma20 = win(px['ma20'], si, di)[0]
    slope = win(px['ma20_slope5'], si, di)[0]

    hv_close = g['close'][si, di].astype('float64')[:, None]
    hv_high = g['high'][si, di].astype('float64')[:, None]

    # --- structure / digestion, day by day ------------------------------
    # Section 9's two predicates, re-read at every column: bull_hold is the
    # 'still running' state, a close below 0.90 x HVE close is the break.
    bull = ((cl > ma20) & (slope > 0) & (cl >= 0.95 * hv_close) & okw)
    del ma20, slope
    keep = (cl >= 0.90 * hv_close) & okw
    digest_day = (~bull & keep)                 # digested, structure intact
    del bull
    # cumulative views; column j counts sessions t0+1 .. t0+j+1
    digest_cum = np.cumsum(digest_day, axis=1).astype('int32')
    nobreak_cum = np.logical_and.accumulate(keep, axis=1)
    # 'strictly before the breakout session' = the same view, shifted by one
    digest_before = _shift1(digest_cum, np.zeros(E, dtype='int32'))
    nobreak_before = _shift1(nobreak_cum, np.ones(E, dtype=bool))
    del digest_cum, nobreak_cum, digest_day, keep

    # --- volume contraction (section 11's pre-registered setting) -------
    ma5 = _roll_mean(g['vol'], 5)
    vma20 = np.where(ind['VOL_MA20'] > 0, g['vol'] / ind['VOL_MA20'], np.nan)
    m_m5 = win(ma5, si, di)[0] / win(vma20, si, di)[0]
    del ma5, vma20
    ct_flag, ct_end = contraction_hit(m_m5[:, :W], CT_D, CT_THR)
    del m_m5
    # ct_end is the 1-based session at which the contraction run ENDS; it is
    # strictly earlier than the breakout when ct_end <= the session before it
    cols = np.arange(KW)[None, :]
    ct_before = (ct_end[:, None] > 0) & (ct_end[:, None] <= cols)

    # --- breakout levels ------------------------------------------------
    hi20 = win(_roll_max_prev(g['high'], 20), si, di)[0]
    brk20 = (cl > hi20) & okw
    hi40 = win(_roll_max_prev(g['high'], 40), si, di)[0]
    brk40 = (cl > hi40) & okw
    hi10 = win(_roll_max_prev(g['high'], LOCAL_W), si, di)[0]
    brkloc = (cl > hi10) & okw
    # 'the local high after the HVE' = the highest high from t0 up to the
    # session BEFORE the column being tested
    hi = win(g['high'], si, di)[0]
    runmax = np.maximum.accumulate(
        np.concatenate([hv_high, hi[:, :-1]], axis=1), axis=1)
    del hi
    brkhve = (cl > runmax) & okw

    vol_ok = (vr >= VOL_MIN) & okw
    del vr

    levels = {'B20': hi20, 'B40': hi40, 'B_HVE': runmax, 'B_LOCAL': hi10}
    brks = {'B20': brk20, 'B40': brk40, 'B_HVE': brkhve, 'B_LOCAL': brkloc}
    return {'cl': cl, 'okw': okw, 'levels': levels, 'brks': brks,
            'vol_ok': vol_ok, 'digest_before': digest_before,
            'nobreak_before': nobreak_before, 'ct_before': ct_before,
            'ct_flag': ct_flag, 'E': E}


def main():
    g = build_grid(lg=lg)
    ind, px, ma = build_indicators(g, lg=lg)
    ev = pd.read_parquet(EV_IN)
    si = ev['s_i'].to_numpy()
    di = ev['t0'].to_numpy()
    yr = ev['year'].to_numpy()
    E = len(ev)
    lg('primary events %d  (breakout search window %d sessions)'
       % (E, W))
    N = g['close'].shape[1]

    w7 = build_w7(g, ind, px, si, di)
    cl, okw = w7['cl'], w7['okw']
    digest_before = w7['digest_before']
    nobreak_before = w7['nobreak_before']
    ct_before = w7['ct_before']
    vol_ok = w7['vol_ok']

    # ---- section 13: the chain, one link at a time ----------------------
    # `ok` is essential on every stage: digest_before / ct_before /
    # nobreak_before are all shifted or cumulative, so past the panel edge
    # they keep reading the last real session and would fire at a column
    # that does not exist.  Note also that `&` binds tighter than `>=` in
    # Python, so the comparison must be parenthesised or the whole gate
    # silently becomes one big elementwise `>=`.
    ok = okw
    pre_digest = (digest_before >= DIGEST_MIN) & ok
    pre_ct = pre_digest & ct_before & ok
    pre_st = pre_ct & nobreak_before
    stages = {}
    c, h = _first(pre_digest, W)
    stages['W1_DIGESTION'] = (h, c)
    c2, h2 = _first(pre_ct, W)
    stages['W2_CONTRACTION'] = (h2, c2)
    c3, h3 = _first(pre_st, W)
    stages['W3_STRUCTURE'] = (h3, c3)
    for nm in PREREG['w7_chain'][1:]:
        s, c_ = stages[nm]
        lg('%-16s reached %6d / %d  (%.2f%%)' % (nm, int(s.sum()), E,
                                                 100.0 * s.sum() / E))
    lg('contraction at the pre-registered setting (%s<=%s for %d): %d events'
       % (PREREG['contraction_metric_primary'], CT_THR, CT_D,
          int(w7['ct_flag'].sum())))

    # ---- section 14: the four breakout definitions ----------------------
    bd = {}
    for d in DEFS:
        gate = (w7['brks'][d] & vol_ok & (digest_before >= DIGEST_MIN)
                & ct_before & nobreak_before & ok)
        col, hit = _first(gate, W)
        bd[d] = (hit, col)
        lg('%-8s breakout %6d / %d  (%.2f%%)  median wait %s'
           % (d, int(hit.sum()), E, 100.0 * hit.sum() / E,
              int(np.median(col[hit])) if hit.any() else 'n/a'))

    # ---- outcomes -------------------------------------------------------
    rows04 = []
    LA = SEL_CLEAN
    ref_r = {h_: fwd_ret(g, si, di, h_) for h_ in HORIZONS}
    for h_ in HORIZONS:
        rows04.append(block('W0_HVE_h%d' % h_, 'CHAIN', np.ones(E, bool),
                            ref_r[h_], yr, lookahead=LA, horizon=h_,
                            stage='W0_HVE', entry='close of t0'))
    for nm in PREREG['w7_chain'][1:]:
        s, c_ = stages[nm]
        ed = np.where(s, di + c_, di)
        mfe, mae = mfe_mae_at(g, si, ed, TAIL_H)
        for h_ in HORIZONS:
            row = block('%s_h%d' % (nm, h_), 'CHAIN', s,
                        fwd_ret(g, si, ed, h_), yr, lookahead=LA,
                        horizon=h_, stage=nm, entry='first session of the '
                        'stage (%d-session window)' % W)
            row['mfe20_med'] = (float(np.nanmedian(mfe[s])) if s.any()
                                else np.nan)
            row['mae20_med'] = (float(np.nanmedian(mae[s])) if s.any()
                                else np.nan)
            rows04.append(row)
        del mfe, mae, ed, s, c_

    for d in DEFS:
        s, c_ = bd[d]
        ed = np.where(s, di + c_, di)
        mfe, mae = mfe_mae_at(g, si, ed, TAIL_H)
        for h_ in HORIZONS:
            row = block('%s_h%d' % (d, h_), 'BREAKOUT_DEF', s,
                        fwd_ret(g, si, ed, h_), yr, lookahead=LA,
                        horizon=h_, breakout=d,
                        entry='breakout day close')
            row['mfe20_med'] = (float(np.nanmedian(mfe[s])) if s.any()
                                else np.nan)
            row['mae20_med'] = (float(np.nanmedian(mae[s])) if s.any()
                                else np.nan)
            rows04.append(row)
        lg('%-8s breakout-day h10 mean %+.4f' % (
            d, float(np.nanmean(fwd_ret(g, si, ed, 10)[s]))))
        del mfe, mae, ed, s, c_

    for h_ in HORIZONS:
        rows04.append(block('HVE_entry_t0_h%d' % h_, 'REFERENCE',
                            np.ones(E, bool), ref_r[h_], yr, lookahead=LA,
                            horizon=h_, entry='close of t0'))
    del ref_r

    df4 = pd.DataFrame(rows04)
    save_csv(df4, '04_w7_breakout_analysis.csv')
    lg('04 rows written %d ; sections %s'
       % (len(df4), sorted(df4['section'].unique())))

    # ---- sections 15 / 28: the entry comparison -------------------------
    # Entry sessions are gathered on the panel, so an entry that would fall
    # on or before the HVE day is dropped from that variant and counted.
    lo = win(g['low'], si, di)[0]
    clw = cl
    rows05 = []
    idx = np.arange(E)
    counts = {}
    for d in DEFS:
        s, c_ = bd[d]
        level = np.where(s, w7['levels'][d][idx, np.clip(c_ - 1, 0, KW - 1)],
                         np.nan)
        # ---- E1/E2: fixed offsets from the breakout session ----
        for v in ENTRIES:
            if v == 'ENTRY_PULLBACK':
                continue
            lag = PRE_LAG[v]
            off = c_ - lag
            # an entry must exist on the panel: at or after t0+1 and at or
            # before the last session.  ENTRY_BD1 is the one that can fall
            # past the edge, so the bound is part of the selection, not a
            # clip -- clipping would book a return at the wrong session.
            okv = s & (off >= 1) & ((di + off) <= N - 1)
            ed = np.clip(np.where(okv, di + off, di), 0, N - 1)
            # only the entries taken BEFORE the breakout are selected on a
            # breakout that had not happened yet
            sel_look = SEL_LOOK if lag > 0 else SEL_CLEAN
            mfe, mae = mfe_mae_at(g, si, ed, TAIL_H)
            for h_ in HORIZONS:
                row = block('%s|%s|h%d' % (d, v, h_), 'ENTRY', okv,
                            fwd_ret(g, si, ed, h_), yr, lookahead=LA,
                            horizon=h_, breakout=d, entry=v,
                            selection=sel_look)
                row['mfe20_med'] = (float(np.nanmedian(mfe[okv]))
                                    if okv.any() else np.nan)
                row['mae20_med'] = (float(np.nanmedian(mae[okv]))
                                    if okv.any() else np.nan)
                rows05.append(row)
            counts['%s|%s' % (d, v)] = int(okv.sum())
            del mfe, mae, okv, ed

        # ---- E3: the pullback that holds ----
        found = np.zeros(E, dtype=bool)
        pe = np.zeros(E, dtype='int32')
        for q in range(1, PW + 1):
            col = c_ - 1 + q
            inr = s & ~found & (col < KW)
            cc = np.clip(col, 0, KW - 1)
            touch = lo[idx, cc] <= level
            holds = clw[idx, cc] >= TOL * level
            now = inr & touch & holds
            pe = np.where(now, di + q, pe)
            found |= now
        pe = np.where(found, pe, di).astype('int32')
        mfe, mae = mfe_mae_at(g, si, pe, TAIL_H)
        for h_ in HORIZONS:
            row = block('%s|ENTRY_PULLBACK|h%d' % (d, h_), 'ENTRY', found,
                        fwd_ret(g, si, pe, h_), yr, lookahead=LA, horizon=h_,
                        breakout=d, entry='ENTRY_PULLBACK',
                        selection=SEL_CLEAN)
            row['mfe20_med'] = (float(np.nanmedian(mfe[found]))
                                if found.any() else np.nan)
            row['mae20_med'] = (float(np.nanmedian(mae[found]))
                                if found.any() else np.nan)
            rows05.append(row)
        counts['%s|ENTRY_PULLBACK' % d] = int(found.sum())
        lg('%-8s pullback found %6d / %d  (%.2f%%)'
           % (d, int(found.sum()), int(s.sum()),
              100.0 * found.sum() / max(1, int(s.sum()))))
        del mfe, mae, found, pe, touch, holds, level

    del lo, clw, cl, okw, w7

    df5 = pd.DataFrame(rows05)
    save_csv(df5, '05_w7_entry_comparison.csv')
    lg('05 rows written %d ; sections %s'
       % (len(df5), sorted(df5['section'].unique())))

    save_json({
        'n_events': int(E),
        'breakout_window': W,
        'digest_min_days': DIGEST_MIN,
        'vol_min': VOL_MIN,
        'pullback': {'max_wait': PW, 'tol': TOL},
        'contraction': [PREREG['contraction_metric_primary'], CT_THR, CT_D],
        'chain_coverage': {nm: int(stages[nm][0].sum())
                           for nm in PREREG['w7_chain'][1:]},
        'chain_conditions': {
            'W1_DIGESTION': 'parse 1: >= %d sessions before the tested '
                            'session that are NOT bull hold while the close '
                            'still holds the HVE structure' % DIGEST_MIN,
            'W2_CONTRACTION': 'W1 and the pre-registered volume contraction '
                              'has COMPLETED strictly before the session',
            'W3_STRUCTURE': 'W2 and the close never dropped below 0.90 x the '
                            'HVE close up to that session'},
        'breakout_coverage': {d: int(bd[d][0].sum()) for d in DEFS},
        'breakout_definitions': {
            'B20': 'close > the prior 20-session high',
            'B40': 'close > the prior 40-session high',
            'B_HVE': 'close > the highest high from the HVE day up to the '
                     'previous session',
            'B_LOCAL': 'close > the prior %d-session high (the consolidation '
                       'range)' % LOCAL_W},
        'breakout_gate': 'the breakout must also print vol/MA20(vol) >= %s '
                         'and satisfy the full W7 chain' % VOL_MIN,
        'w7_definition': 'PREREG section 13/14; the chain minus the breakout '
                         'is W3_STRUCTURE, and every breakout definition is '
                         'measured on top of it',
        'entry_variants': {v: ('first session of t0+1..t0+%d (no breakout '
                               'required yet)' % W) if v in ('ENTRY_PRE5',
                                                             'ENTRY_PRE3',
                                                             'ENTRY_PRE1')
                           else v for v in ENTRIES},
        'entry_counts': counts,
        'selection_lookahead': {
            SEL_LOOK: 'the event set is defined by a breakout that has not '
                      'happened yet at the entry session, so the row is a '
                      'CONDITIONAL comparison of entries and is not a '
                      'tradable specification on its own',
            SEL_CLEAN: 'the entry session is at or after the breakout, so '
                       'nothing in the row is selected on later prices'},
        'cost_basis': 'r_* are gross, r_mean_net30 / r_pf_net30 net of %d bp'
                      % PREREG['primary_cost_bp'],
        'phase_definition': {'IS': list(Q_IS), 'VALID': list(Q_VALID),
                             'OOS': list(Q_OOS)},
        'read_note': 'section 28 does not ask which entry has the highest '
                     'mean; it asks which entry has the most stable OOS risk '
                     'profile.  Compare r_mean_OOS against r_mean_IS and the '
                     'net30 rows, not the gross mean.',
    }, '04_w7_summary.json')


if __name__ == '__main__':
    main()
