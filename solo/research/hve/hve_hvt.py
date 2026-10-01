# -*- coding: utf-8 -*-
"""hve_hvt -- spec section 12 (and the HVT-BULL half of section 16).

Deliverable
    03_hvt_bull_analysis.csv

    第12章  HVT-BULL is built as a nested ladder on top of the HVE and each
            step is measured for what it adds:
                L0 HVE
                L1 + close > MA20                       (bullish structure)
                L2 + MA20 slope > 0                     (bullish structure)
                L3 + close > HVE close                  (post-HVE strength)
                L4 + close > HVE high                   (post-HVE strength)
                L5 + no structural damage               (never broke -10%)
                L6 + re-expansion                       (volume comes back)
            L6 is the HVT-BULL definition used by the section-16 table.
            Each of the six section-12 candidate conditions is also measured
            on its own, and the two continuous ones -- distance to the prior
            20-day high and drawdown from the HVE close -- are graded into
            buckets instead of being collapsed to one threshold.

Entry rule (pre-registered, PREREG['hvt_entry_rule'])
    A condition set is entered at the FIRST session of t0+1 .. t0+20 on
    which every condition holds, evaluated with information up to that
    session only.  Every condition is a level or a running statistic at the
    session being tested, so no future price enters the label; the forward
    returns start at that session's close.  L0 has no confirmation day and
    is entered at the HVE close.  An event that never confirms is dropped
    from the layer -- it is not held as cash, and it is not measured with a
    later entry.

Discipline
    * No composite score, no weights, no "best parameter": the ladder order
      is the order the specification lists the components in, and it was
      fixed before this file first ran.
    * Coverage is reported next to every expectancy.  A gate that keeps 2%
      of events and lifts the mean is not alpha until it survives the
      sections that follow (17-27), so the count is never hidden.
    * MFE / MAE are measured from the entry session, matching the returns.
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hve_common import (PREREG, Log, build_grid, build_indicators,   # noqa
                        save_csv, save_json, DATA, Q_IS, Q_VALID, Q_OOS,
                        _roll_max_prev, fwd_ret, win_post,            # noqa
                        ledger_block, mfe_mae_at)                     # noqa

lg = Log('hve_hvt')

EV_IN = os.path.join(DATA, 'hve_path_events.parquet')
F = PREREG['hvt_confirm_max_wait']
HORIZONS = PREREG['horizons']
TAIL_H = PREREG['hvt_tail_horizon']
TAIL_MOVE = PREREG['hvt_tail_move']
DIST_BUCKETS = PREREG['hvt_dist_hi20_buckets']
MDD_BUCKETS = PREREG['hvt_maxdd_buckets']

block = ledger_block


def win(M, si, di, K=F):
    return win_post(M, si, di, K)


# ------------------------------------------------------------- conditions
def build_conditions(g, ind, px, si, di):
    """The section-12 candidate conditions over t0+1 .. t0+F.

    Each entry is an (E, F) boolean array; column j is session t0+j+1.  Every
    one is a function of information at or before that session:

      T_CLOSE_GT_MA20        close > MA20
      T_MA20_SLOPE_UP        MA20 slope over the last 5 sessions > 0
      S_CLOSE_GT_HVE_CLOSE   close > the HVE close      (post-HVE strength)
      S_CLOSE_GT_HVE_HIGH    close > the HVE high       (post-HVE strength)
      N_NO_STRUCTURAL_DAMAGE running low of close/HVE_close >= 0.90
      R_REEXPANSION          close > MA20 and vol/MA20(vol) >= 1 and
                             close > the prior 10-session high and the
                             previous session was NOT already in bull hold
                             (i.e. the expansion follows a digestion)

    Also returned, for the graded view:
      dist_hi20  close / prior-20-session-high - 1   at each session
      dd_run     running low of close/HVE_close - 1  at each session
    """
    cl = win(g['close'], si, di)[0]
    okw = win(g['close'], si, di)[1]
    ma20 = win(px['ma20'], si, di)[0]
    slope = win(px['ma20_slope5'], si, di)[0]
    vma = win(ind['VOL_MA20'], si, di)[0]
    hi20 = win(px['hi20'], si, di)[0]
    hi10_src = _roll_max_prev(g['high'], 10)
    hi10 = win(hi10_src, si, di)[0]
    del hi10_src

    hv_close = g['close'][si, di].astype('float64')[:, None]
    hv_high = g['high'][si, di].astype('float64')[:, None]
    ratio = np.where(okw & (hv_close > 0), cl / hv_close, np.nan)
    dd_run = np.fmin.accumulate(np.where(np.isfinite(ratio), ratio, np.inf),
                                axis=1) - 1.0
    with np.errstate(invalid='ignore'):
        dist_hi20 = np.where(okw & (hi20 > 0), cl / hi20 - 1.0, np.nan)

    bull_hold = ((cl > ma20) & (slope > 0) &
                 (cl >= 0.95 * hv_close) & okw)
    # the session before t0+1 is the HVE day itself, so column 0 is seeded
    # from t0's own values rather than from the first post-event session
    bull_t0 = ((g['close'][si, di] > px['ma20'][si, di]) &
               (px['ma20_slope5'][si, di] > 0))
    prev_bull = np.empty_like(bull_hold)
    prev_bull[:, 0] = bull_t0
    prev_bull[:, 1:] = bull_hold[:, :-1]

    cond = {}
    cond['T_CLOSE_GT_MA20'] = (cl > ma20) & okw
    cond['T_MA20_SLOPE_UP'] = (slope > 0) & okw
    cond['S_CLOSE_GT_HVE_CLOSE'] = (cl > hv_close) & okw
    cond['S_CLOSE_GT_HVE_HIGH'] = (cl > hv_high) & okw
    cond['N_NO_STRUCTURAL_DAMAGE'] = (dd_run >= -0.10) & okw
    cond['R_REEXPANSION'] = ((cl > ma20) & (vma >= 1.0) & (cl > hi10) &
                             ~prev_bull & okw)
    return cond, dist_hi20, dd_run


def first_hit(conds, di):
    """First session in t0+1..t0+F satisfying every condition.

    Returns (hit, j, entry): `j` is the 0-based offset inside the window and
    `entry` the panel index.  Misses fall back to offset 0 and to the event
    day so that gathers stay in range; the hit mask, never the fallback,
    decides which events enter a layer.
    """
    allc = conds[0].copy()
    for c in conds[1:]:
        allc &= c
    hit = allc.any(axis=1)
    j = np.argmax(allc, axis=1)
    return hit, j, np.where(hit, di + j + 1, di).astype('int32')


LADDER = (
    ('L0_HVE', ()),
    ('L1_TREND', ('T_CLOSE_GT_MA20',)),
    ('L2_TREND_UP', ('T_CLOSE_GT_MA20', 'T_MA20_SLOPE_UP')),
    ('L3_STRENGTH_CLOSE', ('T_CLOSE_GT_MA20', 'T_MA20_SLOPE_UP',
                           'S_CLOSE_GT_HVE_CLOSE')),
    ('L4_STRENGTH_HIGH', ('T_CLOSE_GT_MA20', 'T_MA20_SLOPE_UP',
                          'S_CLOSE_GT_HVE_CLOSE', 'S_CLOSE_GT_HVE_HIGH')),
    ('L5_NO_DAMAGE', ('T_CLOSE_GT_MA20', 'T_MA20_SLOPE_UP',
                      'S_CLOSE_GT_HVE_CLOSE', 'S_CLOSE_GT_HVE_HIGH',
                      'N_NO_STRUCTURAL_DAMAGE')),
    ('L6_REEXPANSION', ('T_CLOSE_GT_MA20', 'T_MA20_SLOPE_UP',
                        'S_CLOSE_GT_HVE_CLOSE', 'S_CLOSE_GT_HVE_HIGH',
                        'N_NO_STRUCTURAL_DAMAGE', 'R_REEXPANSION')),
)


def main():
    g = build_grid(lg=lg)
    ind, px, ma = build_indicators(g, lg=lg)
    ev = pd.read_parquet(EV_IN)
    si = ev['s_i'].to_numpy()
    di = ev['t0'].to_numpy()
    yr = ev['year'].to_numpy()
    E = len(ev)
    # ma20_ratio_h1 is the RATIO close/MA20, so the deviation must subtract
    # 1 before it is shown as a percentage.
    lg('primary events %d  (median t0+1 close sits %+.2f%% away from MA20)'
       % (E, 100.0 * (float(np.nanmedian(ev['ma20_ratio_h1'].to_numpy()))
                      - 1.0)))

    cond, dist_hi20, dd_run = build_conditions(g, ind, px, si, di)

    # ---- the ladder ----------------------------------------------------
    sel, entry, wait = {}, {}, {}
    sel['L0_HVE'] = np.ones(E, dtype=bool)
    entry['L0_HVE'] = di.astype('int32')
    wait['L0_HVE'] = np.zeros(E, dtype='int32')
    for name, keys in LADDER:
        if not keys:
            continue
        hit, j, ed = first_hit([cond[k] for k in keys], di)
        sel[name], entry[name], wait[name] = hit, ed, np.where(hit, j + 1, 0)
        lg('%-18s confirmed %6d / %d  (%.2f%%)  median wait %s sessions'
           % (name, int(hit.sum()), E, 100.0 * hit.sum() / E,
              int(np.median(wait[name][hit])) if hit.any() else 'n/a'))

    # ---- the six conditions on their own -------------------------------
    singles = {}
    for k in PREREG['hvt_conditions']:
        hit, j, ed = first_hit([cond[k]], di)
        singles[k] = (hit, ed, np.where(hit, j + 1, 0))
        lg('%-24s alone  %6d / %d' % (k, int(hit.sum()), E))

    # ---- per-layer outcome columns -------------------------------------
    def gather(arr2d, name):
        jj = np.clip(wait[name] - 1, 0, F - 1)
        return arr2d[np.arange(E), jj]

    rows = []
    LA = 'none'        # every entry here is causal; nothing overlaps an outcome
    agg = {}
    for name in PREREG['hvt_layers']:
        s, ed = sel[name], entry[name]
        mfe, mae = mfe_mae_at(g, si, ed, TAIL_H)
        # an event that runs off the panel has no tail observation; it is
        # dropped rather than counted as a miss
        tail = np.where(np.isfinite(mfe),
                        (mfe >= TAIL_MOVE).astype('float64'), np.nan)
        extra = {'layer': name, 'n_layer': int(s.sum()),
                 'wait_med': float(np.median(wait[name][s])) if s.any()
                 else np.nan}
        agg[name] = {'n': int(s.sum()),
                     'mfe20_med': float(np.nanmedian(mfe[s])) if s.any()
                     else np.nan,
                     'mae20_med': float(np.nanmedian(mae[s])) if s.any()
                     else np.nan}
        for h in HORIZONS:
            r = fwd_ret(g, si, ed, h)
            row = block('%s_h%d' % (name, h), 'LADDER', s, r, yr,
                        lookahead=LA, horizon=h, **extra)
            row['mfe20_med'] = agg[name]['mfe20_med']
            row['mae20_med'] = agg[name]['mae20_med']
            rows.append(row)
        rows.append(block('%s|MFE%d>=%d%%' % (name, TAIL_H,
                                              int(TAIL_MOVE * 100)),
                          'RIGHT_TAIL', s, tail, yr, lookahead=LA,
                          layer=name, n_layer=int(s.sum())))
        del mfe, mae, tail

    for k in PREREG['hvt_conditions']:
        s, ed, w_ = singles[k]
        mfe, mae = mfe_mae_at(g, si, ed, TAIL_H)
        for h in HORIZONS:
            r = fwd_ret(g, si, ed, h)
            row = block('%s_h%d' % (k, h), 'CONDITION', s, r, yr,
                        lookahead=LA, horizon=h, condition=k,
                        n_cond=int(s.sum()),
                        wait_med=float(np.median(w_[s])) if s.any() else np.nan)
            row['mfe20_med'] = float(np.nanmedian(mfe[s])) if s.any() else np.nan
            row['mae20_med'] = float(np.nanmedian(mae[s])) if s.any() else np.nan
            rows.append(row)
        lg('%-24s alone h10 mean %+.4f'
           % (k, float(np.nanmean(fwd_ret(g, si, ed, 10)[s]))))
        del mfe, mae

    # ---- graded view of the two continuous conditions -------------------
    # Measured at the entry session of the trend-up layer: the bucket is
    # known when the trade is taken, the return starts after it.
    base_s = sel['L2_TREND_UP']
    base_ed = entry['L2_TREND_UP']
    r10_base = fwd_ret(g, si, base_ed, 10)
    for nm, arr, bk in (('dist_hi20', dist_hi20, DIST_BUCKETS),
                        ('maxdd_from_HVE_close', dd_run, MDD_BUCKETS)):
        v = gather(arr, 'L2_TREND_UP')
        edges = np.concatenate(([-np.inf], np.asarray(bk, dtype='float64'),
                                [np.inf]))
        for i in range(len(edges) - 1):
            lo_e, hi_e = edges[i], edges[i + 1]
            s = base_s & np.isfinite(v) & (v > lo_e) & (v <= hi_e)
            tag = '%s (%s, %s]' % (nm,
                                   '-inf' if not np.isfinite(lo_e)
                                   else '%.2f' % lo_e,
                                   'inf' if not np.isfinite(hi_e)
                                   else '%.2f' % hi_e)
            rows.append(block(tag, 'MODULATOR', s, r10_base, yr,
                              lookahead=LA, horizon=10, variable=nm,
                              lo=None if not np.isfinite(lo_e) else lo_e,
                              hi=None if not np.isfinite(hi_e) else hi_e,
                              n_base=int(base_s.sum())))
        lg('modulator %-22s buckets %s' % (nm, [
            int((base_s & np.isfinite(v) & (v > edges[i]) &
                 (v <= edges[i + 1])).sum()) for i in range(len(edges) - 1)]))
    del base_s, base_ed, r10_base

    # ---- reference: the unfiltered HVE at a fixed entry -----------------
    for h in HORIZONS:
        rows.append(block('HVE_entry_t0_h%d' % h, 'REFERENCE',
                          np.ones(E, bool), fwd_ret(g, si, di, h), yr,
                          lookahead=LA, horizon=h, entry='close of t0'))
        rows.append(block('HVE_entry_t0p1_h%d' % h, 'REFERENCE',
                          np.ones(E, bool), fwd_ret(g, si, di + 1, h), yr,
                          lookahead=LA, horizon=h, entry='close of t0+1'))

    df = pd.DataFrame(rows)
    save_csv(df, '03_hvt_bull_analysis.csv')
    save_json({
        'n_events': int(E),
        'confirm_max_wait': F,
        'horizons': list(HORIZONS),
        'entry_rule': PREREG['hvt_entry_rule'],
        'layers': {n: {'conditions': list(k), 'n': agg[n]['n'],
                       'coverage': agg[n]['n'] / float(E),
                       'mfe20_med': agg[n]['mfe20_med'],
                       'mae20_med': agg[n]['mae20_med']}
                   for n, k in LADDER},
        'conditions_alone': {k: int(singles[k][0].sum())
                             for k in PREREG['hvt_conditions']},
        'hvt_bull_definition': 'L6_REEXPANSION',
        'right_tail_rule': 'MFE over the %d sessions from entry >= %+.0f%%'
                           % (TAIL_H, TAIL_MOVE * 100),
        'modulator_note': 'dist_hi20 and the drawdown from the HVE close are '
                          'read at the entry session of L2_TREND_UP; the '
                          'bucket is observable when the trade is taken and '
                          'the return starts afterwards, so these rows are '
                          'evidence, not a description of the path.',
        'coverage_note': 'the ladder is a filter: each step keeps a strict '
                         'subset of the previous one, and the step count is '
                         'reported on every row.  A layer that raises the '
                         'mean while keeping a small fraction is not alpha '
                         'until sections 17-27 accept it.',
        'l0_note': 'L0 is the unfiltered HVE entered at the HVE close; it has '
                   'no confirmation day, which is why its n equals the whole '
                   'event set rather than a confirmed subset.',
        'state_source': 'hve_path_events.parquet (section 8/9/11 columns) + '
                        'the PIT indicators rebuilt here',
        'cost_basis': 'r_* are gross, r_mean_net30 / r_pf_net30 net of %d bp'
                      % PREREG['primary_cost_bp'],
        'phase_definition': {'IS': list(Q_IS), 'VALID': list(Q_VALID),
                             'OOS': list(Q_OOS)},
    }, '03_hvt_bull_summary.json')
    lg('rows written %d ; sections %s'
       % (len(df), sorted(df['section'].unique())))


if __name__ == '__main__':
    main()
