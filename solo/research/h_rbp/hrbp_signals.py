# -*- coding: utf-8 -*-
"""H-RBP-01 step 2 -- the signal engine (groups A / B / C / D).

One chunked pass over the anchor events evaluates, for every event:

    C   : first retracement touch inside [t0+1, t0+60]         -> T+1 open
    D   : retracement + structure + re-strength (spec 9.1)     -> T+1 open
    B   : a uniformly random eligible session after the event  -> T+1 open
    A   : a uniformly random eligible session, SAME stock and
          SAME calendar year (paired null)                     -> T+1 open
    CF2 : a uniformly random session AFTER the retracement day -> T+1 open

Every position is decided with data at or before the decision session, and
the fill is always the NEXT session's open (A-share T+1).  The `r*` columns
are outcome labels; they are never read by any selection expression.
"""
import os

import numpy as np
import pandas as pd

from hrbp_common import (H, PREREG_H, DATA, Log, eligibility_h, gather_win,
                         vr20_excl_panel, pivot_low_panel, ret_from_open,
                         ret_from_close, first_from, regime_by_day, save_csv,
                         save_json)

W = PREREG_H['window']
BACK = PREREG_H['lookback']
A0 = BACK + 1                    # ext column of session t0+1
B1 = BACK + 1 + W                # one past the last window column
CLIP_BACK = 300
CHUNK = 20000
HOR = PREREG_H['horizons']
PH = PREREG_H['primary_horizon']
C30 = PREREG_H['primary_cost_bp']

BANDS = PREREG_H['bands']
THRS = PREREG_H['grid_thr_pct']
SG = PREREG_H['struct_grid']
SAG = PREREG_H['struct_at_grid']
RSK = PREREG_H['rs_keys']
PC = PREREG_H['primary_cell']

STORE = ('A_RANDOM', 'B_HVE_RANDOM', 'C_BAND_R1', 'C_BAND_R2', 'C_BAND_R3',
         'C_BAND_R4', 'C_ALL_3PCT', 'CF2_RAND_AFTER_RETR', 'D_PRIMARY',
         'D_SIGNAL_STRUCT', 'D_STRUCT_S1', 'D_STRUCT_S2', 'D_STRUCT_S4',
         'D_RS1', 'D_RS3', 'D_VOLCONF')


# ------------------------------------------------------------------ helpers
def roll_max_prev_rows(M, k):
    """(E,K) -> (E,K): at column w the max of columns w-k .. w-1."""
    d = pd.DataFrame(np.asarray(M, dtype='float64').T)
    r = d.shift(1).rolling(int(k), min_periods=int(k)).max().to_numpy().T
    return r.astype('float32')


def gw_ext(M, si, t0s, fill=np.nan):
    """(E, K) block for sessions t0-BACK .. t0+W."""
    return gather_win(M, si, t0s, BACK, W, fill=fill)[0]


def col_at(M, si, di):
    """M[si, di] with out-of-panel cells flagged NaN / False."""
    N = M.shape[1]
    d = np.asarray(di)
    ok = (d >= 0) & (d <= N - 1)
    dd = np.clip(d, 0, N - 1).astype('int64')
    v = np.asarray(M)[np.asarray(si, dtype='int64'), dd]
    if np.issubdtype(np.asarray(M).dtype, np.bool_):
        return np.where(ok, v, False).astype(bool)
    return np.where(ok, v, np.nan).astype('float32')


def pick_uniform(mask, rng):
    """Uniformly random True column per row; -1 where the row has none."""
    c = np.cumsum(mask, axis=1)
    n = c[:, -1]
    r = rng.random(mask.shape[0]) * n
    idx = np.argmax(c > r[:, None], axis=1)
    return np.where(n > 0, idx, -1).astype('int64')


def entry_of(t0s, pos):
    p = np.asarray(pos, dtype='int64')
    return np.where(p >= 0, np.asarray(t0s, dtype='int64') + p + 2, -1)


def at(M, pos):
    p = np.asarray(pos, dtype='int64')
    j = np.clip(p, 0, M.shape[1] - 1)
    return np.where(p >= 0, M[np.arange(len(p)), j], np.nan)


def book(g, si, ei):
    """Per-trade returns (T+1 open) plus the execution-audit flags."""
    N = g['close'].shape[1]
    ei = np.asarray(ei, dtype='int64')
    valid = (ei >= 0) & (ei <= N - 1)
    c = np.clip(ei, 0, N - 1)
    out = {}
    for h in HOR:
        out['r%d' % h] = ret_from_open(g, si, ei, h).astype('float32')
    out['r%d_ec' % PH] = ret_from_close(g, si, ei, PH).astype('float32')
    out['entry_traded'] = np.where(valid, g['traded'][si, c], False)
    out['entry_oneword'] = np.where(valid, g['oneword'][si, c], False)
    out['entry_limup'] = np.where(valid, g['limup'][si, c], False)
    return out


def build_a_index(g, el, N, lg):
    si_e, di_e = np.nonzero(el)
    keep = di_e <= (N - 2)
    si_e, di_e = si_e[keep], di_e[keep]
    yr = np.array([int(g['dates'][d][:4]) for d in di_e], dtype='int64')
    key = si_e.astype('int64') * 100 + yr
    order = np.argsort(key, kind='stable')
    lg('group A index: %d eligible (stock, year) sessions' % len(key))
    return {'key': key[order], 'day': di_e[order].astype('int64')}


def pick_a(a_map, si, yr, t0s, rng):
    """Same stock, same year, uniform eligible day, kept away from t0 +-5."""
    key = np.asarray(si, dtype='int64') * 100 + np.asarray(yr, dtype='int64')
    lo = np.searchsorted(a_map['key'], key, 'left')
    hi = np.searchsorted(a_map['key'], key, 'right')
    n = (hi - lo).astype('int64')
    out = np.full(len(si), -1, dtype='int64')
    pend = np.flatnonzero(n > 0)
    for _ in range(10):
        if pend.size == 0:
            break
        d = a_map['day'][lo[pend] + rng.integers(0, n[pend])]
        far = np.abs(d - np.asarray(t0s, dtype='int64')[pend]) > 5
        out[pend[far]] = d[far]
        pend = pend[~far]
    if pend.size:
        out[pend] = a_map['day'][lo[pend] + rng.integers(0, n[pend])]
    return out


# ------------------------------------------------------------------- main
def main():
    lg = Log('hrbp_signals')
    lg('H-RBP-01 signal engine   W=%d  back=%d  arms stored=%d'
       % (W, BACK, len(STORE)))
    lg.sep('=')

    g = H.build_grid(lg=lg, use_cache=True)
    ind, px, ma = H.build_indicators(g, lg=lg, use_cache=True)
    el = eligibility_h(g, lg=lg)
    S, N = g['close'].shape
    dates = g['dates']
    month_arr = np.array([int(d[:6]) for d in dates], dtype='int32')
    reg = regime_by_day(g, lg=lg)
    vrp = vr20_excl_panel(g)
    pl = pivot_low_panel(g, PREREG_H['s4_pivot_k'])
    lg('pivot-low panel density %.5f' % float(pl.mean()))

    ev = pd.read_parquet(os.path.join(DATA, 'hrbp_events.parquet'))
    n_all = len(ev)
    ev = ev[ev['t0_last_ok'] & (ev['t0'] >= CLIP_BACK)].reset_index(drop=True)
    si_all = ev['s_i'].to_numpy().astype('int64')
    t0_all = ev['t0'].to_numpy().astype('int64')
    yr_all = ev['year'].to_numpy().astype('int32')
    E = len(ev)
    lg('events in sample %d   (all %d, dropped %d)'
       % (E, n_all, n_all - E))

    a_map = build_a_index(g, el, N, lg)
    rng = np.random.default_rng(PREREG_H['seed'])

    cells = [(thr, sk, sa, rk) for thr in THRS for sk in SG for sa in SAG
             for rk in RSK]
    grid_acc = np.zeros((len(cells), 9, 6), dtype='float64')
    store = {k: [] for k in STORE}
    stat = {'A_no_control': 0, 'B_no_slot': 0, 'CF2_no_slot': 0}

    for a0 in range(0, E, CHUNK):
        b0 = min(E, a0 + CHUNK)
        si, t0s, yr = si_all[a0:b0], t0_all[a0:b0], yr_all[a0:b0]
        Ei = len(si)
        ar = np.arange(Ei)

        cl = gw_ext(g['close'], si, t0s)
        hi = gw_ext(g['high'], si, t0s)
        lo = gw_ext(g['low'], si, t0s)
        ma20 = gw_ext(px['ma20'], si, t0s)
        vr = gw_ext(vrp, si, t0s)
        pv = gw_ext(pl, si, t0s, fill=False)
        ee = gw_ext(el, si, t0s, fill=False)

        cw, hw, lw, mw, vrw = (x[:, A0:B1] for x in (cl, hi, lo, ma20, vr))
        elw = ee[:, A0:B1]

        # ---- event high / drawdown (expanding max, PIT) ----------------
        ehigh = np.fmax.accumulate(
            np.concatenate([hi[:, BACK][:, None], hw], axis=1), axis=1)[:, 1:]
        dd = np.where(cw > 0, cw / np.where(ehigh > 0, ehigh, np.nan) - 1.0,
                      np.nan).astype('float32')

        # ---- structure (spec section 7) --------------------------------
        l0 = lo[:, BACK][:, None]
        s1 = cw >= mw
        s2 = lw >= l0
        s3 = s1 & s2
        ext_low = np.concatenate([l0, lw], axis=1)
        ext_piv = np.concatenate([pv[:, BACK][:, None], pv[:, A0:B1]], axis=1)
        cum = np.maximum.accumulate(
            np.where(ext_piv, ext_low.astype('float64'), -np.inf), axis=1)
        sl = np.full(cum.shape, -np.inf)
        sl[:, 3:] = cum[:, :W - 2]
        slw = sl[:, 1:]
        slw = np.where(np.isfinite(slw), slw, l0.astype('float64'))
        s4 = cw >= slw
        st = {'S1_MA20': s1, 'S2_EVENT_LOW': s2, 'S3_MA20_AND_EVENT_LOW': s3,
              'S4_RECENT_SWING_LOW': s4}

        # ---- re-strength (spec section 8) ------------------------------
        rm3 = roll_max_prev_rows(hi, 3)[:, A0:B1]
        rm5 = roll_max_prev_rows(hi, 5)[:, A0:B1]
        prev_c = cl[:, BACK:BACK + W]
        rs = {'RS1_3D': cw > rm3, 'RS2_5D': cw > rm5,
              'RS3_MA20': (cw > mw) & (cw > prev_c)}
        vconf = vrw >= PREREG_H['volconf_ratio']

        # ---- retracement first-touch -----------------------------------
        pos_band = {nm: first_from((dd <= -lo_) & (dd > -hi_),
                                   np.zeros(Ei, dtype='int16'))
                    for nm, lo_, hi_ in BANDS}
        pos_thr = {t: first_from(dd <= -t / 100.0, np.zeros(Ei, dtype='int16'))
                   for t in THRS}
        pos_all = first_from(dd <= -0.03, np.zeros(Ei, dtype='int16'))

        # ---- group B : random entry inside the post-event window -------
        mB = elw[:, :W - 1] & elw[:, 1:]
        uB = pick_uniform(mB, rng)
        stat['B_no_slot'] += int((uB < 0).sum())
        emit(store, 'B_HVE_RANDOM', g, si, t0s, entry_of(t0s, uB),
             at(dd, uB), yr, month_arr, Ei)

        # ---- group A : paired random event -----------------------------
        dA = pick_a(a_map, si, yr, t0s, rng)
        stat['A_no_control'] += int((dA < 0).sum())
        emit(store, 'A_RANDOM', g, si, t0s, np.where(dA >= 0, dA + 1, -1),
             np.full(Ei, np.nan), yr, month_arr, Ei)

        # ---- group C : retracement only --------------------------------
        for nm, _, _ in BANDS:
            emit(store, 'C_BAND_%s' % nm, g, si, t0s,
                 entry_of(t0s, pos_band[nm]), at(dd, pos_band[nm]),
                 yr, month_arr, Ei)
        emit(store, 'C_ALL_3PCT', g, si, t0s, entry_of(t0s, pos_all),
             at(dd, pos_all), yr, month_arr, Ei)

        # ---- CF2 : random session after the retracement day -------------
        pR1 = pos_band['R1']
        mC = np.zeros((Ei, W), dtype=bool)
        mC[:, :W - 1] = ((np.arange(W - 1)[None, :] > pR1[:, None])
                         & elw[:, :W - 1] & elw[:, 1:])
        uC = pick_uniform(mC, rng)
        stat['CF2_no_slot'] += int(((pR1 >= 0) & (uC < 0)).sum())
        emit(store, 'CF2_RAND_AFTER_RETR', g, si, t0s, entry_of(t0s, uC),
             at(dd, uC), yr, month_arr, Ei)

        # ---- group D : full parameter grid (aggregate only) -------------
        for ci, (thr, sk, sa, rk) in enumerate(cells):
            sp = d_pos(pos_thr[thr], st[sk], rs[rk], None, sa, Ei, W)
            grid_acc_add(grid_acc, ci, yr, g, si,
                         entry_of(t0s, sp), None)

        # ---- named D arms (per-trade storage) --------------------------
        pR1i = pos_band['R1']
        for tag, sk, sa, rk, vc, p in (
                ('D_PRIMARY', PC['struct'], PC['struct_at'], PC['rs'],
                 PC['volconf'], pR1i),
                ('D_SIGNAL_STRUCT', 'S3_MA20_AND_EVENT_LOW', 'SIGNAL',
                 'RS2_5D', False, pR1i),
                ('D_STRUCT_S1', 'S1_MA20', 'RETRACE', 'RS2_5D', False, pR1i),
                ('D_STRUCT_S2', 'S2_EVENT_LOW', 'RETRACE', 'RS2_5D', False,
                 pR1i),
                ('D_STRUCT_S4', 'S4_RECENT_SWING_LOW', 'RETRACE', 'RS2_5D',
                 False, pR1i),
                ('D_RS1', 'S3_MA20_AND_EVENT_LOW', 'RETRACE', 'RS1_3D',
                 False, pR1i),
                ('D_RS3', 'S3_MA20_AND_EVENT_LOW', 'RETRACE', 'RS3_MA20',
                 False, pR1i),
                ('D_VOLCONF', 'S3_MA20_AND_EVENT_LOW', 'RETRACE', 'RS2_5D',
                 True, pR1i)):
            sp = d_pos(p, st[sk], rs[rk], vconf if vc else None, sa, Ei, W)
            emit(store, tag, g, si, t0s, entry_of(t0s, sp), at(dd, sp),
                 yr, month_arr, Ei)

        lg('chunk %6d-%6d  R1 hit %5d  D_PRIMARY %5d'
           % (a0, b0, int((pR1i >= 0).sum()),
              int((entry_of(t0s, d_pos(pR1i, st[PC['struct']], rs[PC['rs']],
                                       None, PC['struct_at'], Ei, W)) >= 0
                   ).sum())))

    # ---- persist -------------------------------------------------------
    tr = pd.concat([pd.concat(v, ignore_index=True) for v in store.values()
                    if v], ignore_index=True)
    idx = np.clip(tr['entry_idx'].to_numpy(), 0, N - 1)
    tr['regime'] = reg[idx]
    tr['phase'] = [phase_of(int(y)) for y in tr['year']]
    tr.to_parquet(os.path.join(DATA, 'hrbp_trades.parquet'), index=False)
    lg('trade table %d rows / %d arms -> hrbp_trades.parquet'
       % (len(tr), tr['arm'].nunique()))
    for tag in STORE:
        n = int((tr.loc[tr['arm'] == tag, 'entry_idx'] >= 0).sum())
        lg('  arm %-22s trades %6d' % (tag, n))

    names = ['n', 'sum_r10', 'sum_r10_n30', 'nwin30', 'spos30', 'sneg30']
    out = []
    for ci, (thr, sk, sa, rk) in enumerate(cells):
        for yi in range(9):
            v = grid_acc[ci, yi]
            if v[0] <= 0:
                continue
            out.append({'cell_id': ci, 'thr_pct': thr, 'struct': sk,
                        'struct_at': sa, 'rs': rk, 'year': 2018 + yi,
                        'n': int(v[0]), 'mean_r10': v[1] / v[0],
                        'mean_r10_net30': v[2] / v[0], 'win30': v[3] / v[0],
                        'pf30': (v[4] / abs(v[5])) if v[5] < 0 else np.inf})
    save_csv(pd.DataFrame(out), '12_hrbp_grid_year.csv')
    lg('grid-year rows %d  (%d cells)' % (len(out), len(cells)))

    save_json({'n_events_all': int(n_all), 'n_events_sample': int(E),
               'n_cells': len(cells), 'stored_arms': list(STORE),
               'resample_fail': stat,
               'entry_rule': PREREG_H['entry_rule'],
               'note': 'r* columns are labels only; never used for selection'},
              '13_hrbp_signals_summary.json')
    lg('done')


def phase_of(y):
    from hrbp_common import phase_of_year
    return phase_of_year(int(y))


def d_pos(p, stx, rsx, vconf, struct_at, Ei, W):
    """Group D signal position: first re-strength session at/after the touch."""
    ok = np.asarray(p, dtype='int64') >= 0
    comb = rsx if vconf is None else (rsx & vconf)
    if struct_at == 'RETRACE':
        ar = np.arange(Ei)
        gate = np.where(ok, stx[ar, np.clip(p, 0, W - 1)], False)
        ok = ok & gate
    else:
        comb = comb & stx
    return first_from(comb, np.where(ok, p, -1))


def emit(store, tag, g, si, t0s, ei, dds, yr, month_arr, Ei):
    if tag not in store:
        return
    ei = np.asarray(ei, dtype='int64')
    N = g['close'].shape[1]
    valid = (ei >= 0) & (ei <= N - 1)
    mon = np.where(valid, month_arr[np.clip(ei, 0, len(month_arr) - 1)], -1)
    d = {'arm': np.full(Ei, tag), 's_i': si.astype('int32'),
         't0': t0s.astype('int32'), 'year': np.asarray(yr, 'int32'),
         'entry_idx': ei.astype('int32'), 'month': mon.astype('int32'),
         'dd_at_sig': np.asarray(dds, 'float32')}
    d.update(book(g, si, ei))
    store[tag].append(pd.DataFrame(d))


def grid_acc_add(acc, ci, yr, g, si, ei, dds):
    r = ret_from_open(g, si, ei, PH)
    net = r - C30 / 10000.0
    ok = np.isfinite(r)
    yi = np.asarray(yr) - 2018
    for k in np.unique(yi[ok]):
        m = ok & (yi == k)
        v = net[m]
        w = v[v > 0]
        l = v[v <= 0]
        acc[ci, k, 0] += float(m.sum())
        acc[ci, k, 1] += float(r[m].sum())
        acc[ci, k, 2] += float(v.sum())
        acc[ci, k, 3] += float((v > 0).sum())
        acc[ci, k, 4] += float(w.sum())
        acc[ci, k, 5] += float(l.sum())


if __name__ == '__main__':
    main()
