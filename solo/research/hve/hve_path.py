# -*- coding: utf-8 -*-
"""hve_path -- spec sections 8 / 9 / 10 / 11.

Deliverable
    02_hve_path_analysis.csv   the post-event path study
    02_hve_path_summary.json   counts, reference setting, audit notes
    hve_path_events.parquet    the per-event table with path / state /
                               contraction columns attached (input for the
                               later incremental models)

    第8章  for every HVE: Return / Max Return / Max Drawdown / Volume
           Evolution / MA20 / MA60 / 20D high / 60D high / distance to the
           prior high, at T+1/3/5/10/20/40/60.
    第9章  every event is placed in exactly one state A / B / C / D.
    第10章 the three-way comparison: contraction-then-re-expansion vs
           sustained high volume vs volume-up decline.
    第11章 the volume-contraction grid: three measures x thresholds
           0.8/0.6/0.5/0.4 x durations 2/3/5/10.

Discipline
    * The contraction grid is reported in full.  Nothing here picks the
      threshold that looks best -- the reference setting was pre-registered
      (PREREG['contraction_primary']) before this file first ran.
    * Every post-event quantity is read at t0+1 .. t0+60.  The event day
      itself is the baseline and is never treated as an observation of the
      path.  Forward returns appear only as outcomes.
    * No composite score, no weighting, no "best parameter".
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hve_common import (PREREG, Log, build_grid, build_indicators,    # noqa
                        path_metrics, perf_stats, save_csv, save_json,
                        DATA, Q_IS, Q_VALID, Q_OOS, _roll_mean,
                        _roll_max_prev, fwd_ret, win_post,            # noqa
                        contraction_hit, ledger_block)                # noqa

lg = Log('hve_path')

EV_IN = os.path.join(DATA, 'hve_primary_events.parquet')
EV_OUT = os.path.join(DATA, 'hve_path_events.parquet')
WINDOW = PREREG['path_horizons'][-1]          # 60 sessions after t0
CAUSAL_H = PREREG['primary_horizon']          # 10-session holding period
CAUSAL_K = (5, 10, 20)                        # decision points after t0

CONTRACT_METRICS = PREREG['contraction_metric']
CONTRACT_THR = PREREG['contraction_ratio']
CONTRACT_DAYS = PREREG['contraction_days']

# `win_post` / `contraction_hit` / `ledger_block` live in hve_common so every
# module reads the post-event window and writes the section-20 ledger the
# same way; nothing is redefined here.  `win` only binds this module's default
# window (60 sessions) to the shared reader.
def win(M, si, di, K=WINDOW):
    return win_post(M, si, di, K)


block = ledger_block    # the shared section-20 ledger


# --------------------------------------------------------- section 9 state
def run_state_machine(g, ind, px, si, di, K=WINDOW):
    """Day-by-day post-event state (spec section 9) -> event-level label.

    Section 9 gives four states; the predicates are the pre-registered ones.
    They are evaluated on t0+1 .. t0+K only, never on the event day.

    Day precedence D > C > A > B.  C must outrank A: a re-expansion day
    satisfies the bull_hold predicate almost by construction, so if A won
    the tie C would be an empty state and section 10 would collapse.  A then
    means 'still in bull continuation', B the fallback while structure holds.

    The registered predicates leave gaps -- any day at 0.90 .. 0.93 x HVE
    close, and any day whose drawdown passes -20% without breaking.  Those
    days inherit the previous day's state so every event still receives
    exactly one label; the inherited days are counted and reported rather
    than silently absorbed.  An event whose very FIRST post-event session is
    unassigned has nothing to inherit and is counted separately; it is
    reported as B_DIGESTION (structure not broken) and flagged.
    """
    E = len(si)
    close_w = win(g['close'], si, di, K)[0]
    low_w = win(g['low'], si, di, K)[0]
    vol_w = win(g['vol'], si, di, K)[0]
    okw = win(g['close'], si, di, K)[1]
    ma20_w = win(px['ma20'], si, di, K)[0]
    slope_w = win(px['ma20_slope5'], si, di, K)[0]
    vma_w = win(ind['VOL_MA20'], si, di, K)[0]        # vol / MA20(vol)
    hi10_src = _roll_max_prev(g['high'], 10)
    hi10_w = win(hi10_src, si, di, K)[0]
    del hi10_src

    hv_close = g['close'][si, di].astype('float64')[:, None]
    hv_low = g['low'][si, di].astype('float64')[:, None]

    keep = (close_w >= hv_low) & (close_w >= 0.93 * hv_close) & okw
    brk = (close_w < 0.90 * hv_close) & okw
    bull = ((close_w > ma20_w) & (slope_w > 0) &
            (close_w >= 0.95 * hv_close)) & okw
    # worst close so far, measured against the HVE close (section 9 note)
    dd = np.fmin.accumulate(np.where(okw, close_w / hv_close - 1.0, np.inf),
                            axis=1)
    mild = (dd > -0.20) & okw
    reexp = ((close_w > ma20_w) & (vma_w >= 1.0) &
             (close_w > hi10_w) & okw)

    prev = np.full(E, 'U', dtype='U1')
    broken = np.zeros(E, dtype=bool)
    first_c = np.zeros(E, dtype='int32')
    first_d = np.zeros(E, dtype='int32')
    n_uncl = np.zeros(E, dtype='int32')
    n_days = {c: np.zeros(E, dtype='int32') for c in 'ABCD'}
    for k in range(K):
        alive = okw[:, k] & ~broken
        st = prev.copy()
        st = np.where(alive & keep[:, k] & mild[:, k], 'B', st)
        st = np.where(alive & bull[:, k], 'A', st)
        st = np.where(alive & (prev == 'B') & reexp[:, k], 'C', st)
        st = np.where(alive & brk[:, k], 'D', st)
        n_uncl += (alive & (st == 'U')).astype('int32')
        for c in 'ABCD':
            n_days[c] += (alive & (st == c)).astype('int32')
        first_c = np.where(alive & (st == 'C') & (first_c == 0), k + 1,
                           first_c)
        first_d = np.where(alive & (st == 'D') & (first_d == 0), k + 1,
                           first_d)
        broken |= alive & brk[:, k]
        prev = np.where(alive, st, prev)

    has_c = first_c > 0
    has_d = first_d > 0
    label = np.where(has_d, 'D_FAIL',
                     np.where(has_c, 'C_RE_EXPANSION',
                              np.where(prev == 'A', 'A_BULL_CONTINUATION',
                                       'B_DIGESTION')))
    started_uncl = (prev == 'U') & ~has_c & ~has_d
    out = {'state': label, 'first_c_k': first_c, 'first_d_k': first_d,
           'n_days_unclassified': n_uncl, 'started_unclassified':
           started_uncl.astype('int32')}
    for c in 'ABCD':
        out['n_days_' + c] = n_days[c]
    return out, prev, vol_w


# -------------------------------------------------------------- aggregation
def block(tag, section, sel, col, yr, **meta):
    """One aggregate row: the section-20 ledger for the selected events.

    `sel` and `col` are full-length event arrays; the phase masks are
    applied to the selected subset (masking the full-length phase array
    with a subset index would silently mis-align the rows).
    """
    idx = np.flatnonzero(np.asarray(sel))
    r = np.asarray(col, dtype='float64')[idx]
    y = yr[idx]
    s0 = perf_stats(r, 0)
    s30 = perf_stats(r, PREREG['primary_cost_bp'])
    d = {'section': section, 'tag': tag, 'n_events': int(len(r))}
    d.update(meta)
    for k in ('mean', 'median', 'win_rate', 'pf', 'tail_share', 'mean_win',
              'mean_loss', 't', 'p95'):
        d['r_' + k] = s0[k]
    d['r_mean_net30'] = s30['mean']
    d['r_pf_net30'] = s30['pf']
    for ph, (a, b) in (('IS', Q_IS), ('VALID', Q_VALID), ('OOS', Q_OOS)):
        m = (y >= a) & (y <= b)
        d['n_' + ph] = int(m.sum())
        d['mean_' + ph] = float(np.nanmean(r[m])) if m.any() else np.nan
    return d


def main():
    g = build_grid(lg=lg)
    ind, px, ma = build_indicators(g, lg=lg)
    ev = pd.read_parquet(EV_IN)
    si = ev['s_i'].to_numpy()
    di = ev['t0'].to_numpy()
    yr = ev['year'].to_numpy()
    lg('primary events %d  (%s .. %s)'
       % (len(ev), ev['t0_date'].min(), ev['t0_date'].max()))

    # ---- section 8: the post-event path --------------------------------
    pm = path_metrics(g, si, di)
    ev = pd.concat([ev, pd.DataFrame({'path_' + k: v for k, v in pm.items()},
                                     index=ev.index)], axis=1)
    cl_w = win(g['close'], si, di)[0]
    px_cols = {}
    for nm, src, rel in (('ma20_ratio', px['ma20'], False),
                         ('ma60_ratio', px['ma60'], False),
                         ('dist_hi20', px['hi20'], True),
                         ('dist_hi60', px['hi60'], True)):
        sv = win(src, si, di)[0]
        ratio = np.where(sv > 0, cl_w / np.where(sv > 0, sv, np.nan), np.nan)
        if rel:
            ratio = ratio - 1.0
        for h in PREREG['path_horizons']:
            px_cols['%s_h%d' % (nm, h)] = ratio[:, h - 1]
    ev = pd.concat([ev, pd.DataFrame(px_cols, index=ev.index)], axis=1)
    del cl_w, px_cols
    lg('section 8 path table built (%d columns)' % len(pm))

    # ---- section 9: state machine --------------------------------------
    st, _prev, vol_w = run_state_machine(g, ind, px, si, di)
    ev = pd.concat([ev, pd.DataFrame(
        {'state': st['state'],
         **{'state_' + k: v for k, v in st.items() if k != 'state'}},
        index=ev.index)], axis=1)
    cnt = {c: int((st['state'] == c).sum()) for c in np.unique(st['state'])}
    lg('section 9 states: %s' % cnt)
    lg('unclassified (inherited) days: %d over %d event-days = %.4f%%'
       % (int(st['n_days_unclassified'].sum()), len(ev) * WINDOW,
          100.0 * st['n_days_unclassified'].sum() / (len(ev) * WINDOW)))
    lg('events whose FIRST post-event session was unassigned: %d'
       % int(st['started_unclassified'].sum()))

    # ---- section 11: the volume-contraction grid -----------------------
    hv_vol = g['vol'][si, di].astype('float64')
    with np.errstate(invalid='ignore', divide='ignore'):
        m_vt = vol_w / np.where(hv_vol > 0, hv_vol, np.nan)[:, None]
        ma5 = _roll_mean(g['vol'], 5)
        ma20v = np.where(ind['VOL_MA20'] > 0, g['vol'] / ind['VOL_MA20'],
                         np.nan)
        m_m5 = win(ma5, si, di)[0] / win(ma20v, si, di)[0]
    del vol_w, ma5, ma20v

    flags, firsts = {}, {}
    for metric in CONTRACT_METRICS:
        for thr in CONTRACT_THR:
            for days in CONTRACT_DAYS:
                key = 'ct_%s_%s_%d' % (metric, thr, days)
                if metric == 'V_t_over_HVE':
                    fl, fi = contraction_hit(m_vt, days, thr)
                elif metric == 'MA5_over_MA20':
                    fl, fi = contraction_hit(m_m5, days, thr)
                else:
                    # min_V_over_HVE is inherently a scalar (the deepest
                    # quiet point), so the registered duration axis is the
                    # horizon the minimum is taken over.  Registered before
                    # the run.
                    seg = m_vt[:, :days]
                    mn = np.fmin.reduce(np.where(np.isfinite(seg), seg,
                                                 np.inf), axis=1)
                    fl = mn <= thr
                    fi = np.where(fl, np.argmin(np.where(np.isfinite(seg),
                                                         seg, np.inf),
                                                axis=1) + 1,
                                  0).astype('int32')
                flags[key], firsts[key] = fl, fi
    ev = pd.concat([ev, pd.DataFrame(flags, index=ev.index)], axis=1)
    lg('section 11 grid stored: %d settings x %d events'
       % (len(flags), len(ev)))

    # ---- section 10: three-way path grouping ---------------------------
    ref_m = PREREG['contraction_metric_primary']
    ref_t, ref_d = PREREG['contraction_primary']
    ref_key = 'ct_%s_%s_%d' % (ref_m, ref_t, ref_d)
    ct_ref, ct_ref_first = flags[ref_key], firsts[ref_key]
    ct_any = np.zeros(len(ev), dtype=bool)
    for fl in flags.values():
        ct_any |= fl
    # a contraction strictly before (or on) the first re-expansion session
    ct_before_c = ct_ref & (st['state'] == 'C_RE_EXPANSION') & \
        (ct_ref_first > 0) & (st['first_c_k'] > 0) & \
        (ct_ref_first <= st['first_c_k'])
    with np.errstate(invalid='ignore'):
        vol5 = np.nanmean(m_vt[:, :5], axis=1)
    is_d = st['state'] == 'D_FAIL'
    early = (st['first_d_k'] > 0) & (st['first_d_k'] <= 10)
    # Primary split keyed to the PRE-REGISTERED contraction setting.  Using
    # 'contraction at any of the 48 settings' instead would make the middle
    # arm almost empty by construction -- a 2-session stretch at 0.8 is
    # satisfied by nearly every event -- and is therefore kept only as the
    # labelled robustness variant below.
    grp = np.where(ct_before_c, 'G_CONTRACT_REEXPAND',
                   np.where(~ct_ref & ~is_d, 'G_SUSTAINED_HIGH_VOLUME',
                            np.where(is_d & early & (vol5 >= 1.0),
                                     'G_VOLUME_UP_DECLINE', 'G_OTHER')))
    grp_any = np.where(ct_any & (st['state'] == 'C_RE_EXPANSION') & ~is_d,
                       'G_CONTRACT_REEXPAND',
                       np.where(~ct_any & ~is_d, 'G_SUSTAINED_HIGH_VOLUME',
                                np.where(is_d & early & (vol5 >= 1.0),
                                         'G_VOLUME_UP_DECLINE', 'G_OTHER')))
    ev = pd.concat([ev, pd.DataFrame(
        {'path_group': grp, 'path_group_anyct': grp_any,
         'ct_ref': ct_ref, 'ct_any': ct_any}, index=ev.index)], axis=1)
    lg('section 10 groups (reference setting): %s'
       % {c: int((grp == c).sum()) for c in np.unique(grp)})
    lg('section 10 groups (any setting): %s'
       % {c: int((grp_any == c).sum()) for c in np.unique(grp_any)})
    del ct_any, ct_before_c, vol5, m_m5, flags, firsts

    ev.to_parquet(EV_OUT, index=False)
    lg('path event table %d x %d -> hve_path_events.parquet'
       % (len(ev), ev.shape[1]))

    # ---- aggregates ----------------------------------------------------
    r10 = ev['path_ret10'].to_numpy(dtype='float64')
    rows = []
    # `lookahead` marks the two kinds of row unambiguously:
    #   'none'    the label is built from t0+1..t0+k and the return is
    #             measured from t0+k forward -- label and outcome do not
    #             overlap (HORIZON, CAUSAL_*).
    #   'overlap' the label is built from the whole t0+1..t0+60 window while
    #             the outcome is the t0 -> t0+10 return.  Such a row is a
    #             PATH DESCRIPTION, not evidence of predictability: a state
    #             that requires price to have risen cannot be compared with
    #             a return that starts before it did.
    LA_NONE, LA_OVER = 'none', 'label_window_overlaps_outcome'
    for h in PREREG['path_horizons']:
        rows.append(block('H%d' % h, 'HORIZON', np.ones(len(ev), bool),
                          ev['path_ret%d' % h].to_numpy(dtype='float64'), yr,
                          lookahead=LA_NONE))
    for nm, col in (('MFE_60', 'path_mfe'), ('MAE_60', 'path_mae'),
                    ('MAXDD_60', 'path_maxdd'), ('MFE_20', 'path_mfe20'),
                    ('MAE_20', 'path_mae20')):
        v = ev[col].to_numpy(dtype='float64')
        rows.append({'section': 'HORIZON', 'tag': nm,
                     'n_events': int(np.isfinite(v).sum()),
                     'lookahead': LA_NONE,
                     'r_mean': float(np.nanmedian(v)),
                     'r_p05': float(np.percentile(v[np.isfinite(v)], 5)),
                     'r_p95': float(np.percentile(v[np.isfinite(v)], 95))})
    for c in ('A_BULL_CONTINUATION', 'B_DIGESTION', 'C_RE_EXPANSION',
              'D_FAIL'):
        rows.append(block(c, 'STATE', st['state'] == c, r10, yr,
                          lookahead=LA_OVER))
    for metric in CONTRACT_METRICS:
        for thr in CONTRACT_THR:
            for days in CONTRACT_DAYS:
                key = 'ct_%s_%s_%d' % (metric, thr, days)
                rows.append(block('%s<=%s|%dd' % (metric, thr, days),
                                  'CONTRACTION', ev[key].to_numpy(bool), r10,
                                  yr, lookahead=LA_OVER, metric=metric,
                                  thr=thr, days=days))
    for c in ('G_CONTRACT_REEXPAND', 'G_SUSTAINED_HIGH_VOLUME',
              'G_VOLUME_UP_DECLINE', 'G_OTHER'):
        rows.append(block(c, 'GROUP', grp == c, r10, yr, lookahead=LA_OVER,
                          variant='ref'))
        rows.append(block(c, 'GROUP_ANYCT', grp_any == c, r10, yr,
                          lookahead=LA_OVER, variant='any'))
    rows.append(block('%s<=%s|%dd' % (ref_m, ref_t, ref_d), 'REFERENCE',
                      ct_ref, r10, yr, lookahead=LA_OVER))
    rows.append(block('no_contraction(ref)', 'REFERENCE', ~ct_ref, r10, yr,
                      lookahead=LA_OVER))

    # ---- the same questions asked without lookahead ---------------------
    # Label from t0+1..t0+k, entry at the close of t0+k, outcome over the
    # next CAUSAL_H sessions.  This is the table that may be read as
    # evidence; the descriptive tables above may not.
    for k in CAUSAL_K:
        st_k, _, _ = run_state_machine(g, ind, px, si, di, K=k)
        ft = fwd_ret(g, si, di + k, CAUSAL_H)
        cf, _ = contraction_hit(m_vt[:, :k], ref_d, ref_t)
        rows.append(block('K%d|ALL' % k, 'CAUSAL_STATE',
                          np.ones(len(ev), bool), ft, yr, lookahead=LA_NONE,
                          k=k))
        for c in ('A_BULL_CONTINUATION', 'B_DIGESTION', 'C_RE_EXPANSION',
                  'D_FAIL'):
            rows.append(block('K%d|%s' % (k, c), 'CAUSAL_STATE',
                              st_k['state'] == c, ft, yr, lookahead=LA_NONE,
                              k=k))
        rows.append(block('K%d|contracted' % k, 'CAUSAL_CT', cf, ft, yr,
                          lookahead=LA_NONE, k=k))
        rows.append(block('K%d|not_contracted' % k, 'CAUSAL_CT', ~cf, ft, yr,
                          lookahead=LA_NONE, k=k))
        lg('causal K=%d: %s | contracted %s'
           % (k, {c: int((st_k['state'] == c).sum()) for c in
                  np.unique(st_k['state'])}, int(cf.sum())))
        del st_k, ft, cf

    df = pd.DataFrame(rows)
    save_csv(df, '02_hve_path_analysis.csv')
    save_json({'window': WINDOW,
               'n_events': int(len(ev)),
               'states': cnt,
               'groups_ref': {c: int((grp == c).sum())
                              for c in np.unique(grp)},
               'groups_anyct': {c: int((grp_any == c).sum())
                                for c in np.unique(grp_any)},
               'contraction_primary': [ref_m, ref_t, ref_d],
               'contraction_settings': len(PREREG['contraction_metric'])
               * len(CONTRACT_THR) * len(CONTRACT_DAYS),
               'unclassified_days': int(st['n_days_unclassified'].sum()),
               'events_started_unclassified':
                   int(st['started_unclassified'].sum()),
               'state_source': 'last observed state (A) / D_FAIL and '
                               'C_RE_EXPANSION are absorbing',
               'group_definition': {
                   'G_CONTRACT_REEXPAND': 'reference-setting contraction '
                                          'completed no later than the first '
                                          're-expansion day',
                   'G_SUSTAINED_HIGH_VOLUME': 'no contraction at the '
                                              'pre-registered setting and '
                                              'never broke structure',
                   'G_VOLUME_UP_DECLINE': 'structure break within 10 '
                                          'sessions while mean volume over '
                                          't0+1..t0+5 >= HVE volume',
                   'G_OTHER': 'everything else'},
               'lookahead_note': 'state labels and contraction flags use '
                                 'sessions t0+1..t0+60 only; the event-day '
                                 'close is the baseline, never a lookahead',
               'evidentiary_sections': {
                   'usable_as_evidence': ['HORIZON', 'CAUSAL_STATE',
                                          'CAUSAL_CT'],
                   'descriptive_only': ['STATE', 'CONTRACTION', 'GROUP',
                                        'GROUP_ANYCT', 'REFERENCE'],
                   'why': 'the descriptive sections build the label from the '
                          'full t0+1..t0+60 window and compare it with the '
                          't0 -> t0+10 return, so label and outcome overlap; '
                          'the CSV carries this in the `lookahead` column. '
                          'CAUSAL_STATE / CAUSAL_CT rebuild the label from '
                          't0+1..t0+k and measure the return from t0+k.',
                   'decision_points_k': list(CAUSAL_K),
                   'holding_sessions': CAUSAL_H},
               'duration_axis_note': 'for V_t_over_HVE and MA5_over_MA20 the '
                                     'duration axis is exactly redundant: a '
                                     '2-session dip below a threshold and a '
                                     '10-session one select the same events. '
                                     'Only min_V_over_HVE, which is a running '
                                     'minimum, changes with the horizon.'},
              '02_hve_path_summary.json')
    lg('rows written %d ; sections %s'
       % (len(df), sorted(df['section'].unique())))


if __name__ == '__main__':
    main()
