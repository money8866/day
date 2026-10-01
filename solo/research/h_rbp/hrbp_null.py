# -*- coding: utf-8 -*-
"""H-RBP-01 step 4 -- null models and counterfactuals.

Three nulls (pre-registered):
    Null 1  random event date, same stock + same calendar year   (arm A)
    Null 2  HVE anchor, uniformly random entry in the window     (arm B)
    Null 3  same stock + SAME calendar month + same holding
            horizon, random eligible entry outside the event window

Two counterfactuals:
    CF-1  real signal vs a random session inside the SAME event window
          (paired, event by event)
    CF-2  the retracement touch vs a random session AFTER that touch
          (paired, event by event)  -> answers "is it the touching, or is
          it simply the passage of time?"
"""
import os

import numpy as np
import pandas as pd

from hrbp_common import (H, PREREG_H, DATA, Log, eligibility_h,
                         ret_from_open, cluster_boot_diff, save_csv, save_json)

HOR = PREREG_H['horizons']
PH = PREREG_H['primary_horizon']
B = PREREG_H['boot_B']
SEED = PREREG_H['seed']
C30 = PREREG_H['primary_cost_bp']
WIN = PREREG_H['window']
BACK = PREREG_H['lookback']

PAIRS = [('C_BAND_R1', 'B_HVE_RANDOM'), ('D_PRIMARY', 'B_HVE_RANDOM'),
         ('C_BAND_R1', 'CF2_RAND_AFTER_RETR'),
         ('D_PRIMARY', 'CF2_RAND_AFTER_RETR')]


def matched_key_index(g, el, N):
    """(stock * 10000 + YYYYMM) -> eligible sessions, sorted."""
    si_e, di_e = np.nonzero(el)
    keep = di_e <= (N - 2)
    si_e, di_e = si_e[keep], di_e[keep]
    ym = np.array([int(g['dates'][d][:6]) for d in di_e], dtype='int64')
    key = si_e.astype('int64') * 10000 + ym
    order = np.argsort(key, kind='stable')
    return {'key': key[order], 'day': di_e[order].astype('int64')}


def pick_matched(amap, keys, f_lo, f_hi, rng, tries=15):
    lo = np.searchsorted(amap['key'], keys, 'left')
    hi = np.searchsorted(amap['key'], keys, 'right')
    n = (hi - lo).astype('int64')
    out = np.full(len(keys), -1, dtype='int64')
    pend = np.flatnonzero(n > 0)
    for _ in range(tries):
        if pend.size == 0:
            break
        d = amap['day'][lo[pend] + rng.integers(0, n[pend])]
        bad = (d >= f_lo[pend]) & (d <= f_hi[pend])
        out[pend[~bad]] = d[~bad]
        pend = pend[bad]
    return out


def pair_block(lg, tag, da, db, rng):
    """Observed vs matched-null for a pair of arms on the SAME events."""
    k = ['s_i', 't0']
    m = da[k + ['r3', 'r5', 'r10', 'r20', 'month', 'entry_idx']].merge(
        db[k + ['r3', 'r5', 'r10', 'r20', 'entry_idx']], on=k,
        suffixes=('_o', '_n'))
    rows = []
    for h in HOR:
        ro = m['r%d_o' % h].to_numpy()
        rn = m['r%d_n' % h].to_numpy()
        oo = np.isfinite(ro)
        nn = np.isfinite(rn)
        both = oo & nn
        obs, lo, hi = cluster_boot_diff(ro[both], m['month'].to_numpy()[both],
                                        rn[both], m['month'].to_numpy()[both],
                                        B=B, seed=SEED)
        rows.append({'family': tag, 'observed_arm': da['arm'].iloc[0],
                     'null_arm': db['arm'].iloc[0], 'horizon': 'T+%d' % h,
                     'n_paired': int(both.sum()),
                     'obs_mean': float(np.mean(ro[both])),
                     'obs_net30': float(np.mean(ro[both]) - C30 / 1e4),
                     'null_mean': float(np.mean(rn[both])),
                     'null_median': float(np.median(rn[both])),
                     'excess': obs, 'ci_lo': lo, 'ci_hi': hi})
    for r in rows:
        lg('  %-22s vs %-20s %-5s n=%6d obs %+.4f null %+.4f excess %+.4f '
           '[%+.4f, %+.4f]' % (tag, r['null_arm'], r['horizon'],
                               r['n_paired'], r['obs_mean'], r['null_mean'],
                               r['excess'], r['ci_lo'], r['ci_hi']))
    return rows


def main():
    lg = Log('hrbp_null')
    lg('H-RBP-01 null models / counterfactuals')
    lg.sep('=')

    g = H.build_grid(lg=None, use_cache=True)
    el = eligibility_h(g, lg=lg)
    S, N = g['close'].shape
    dates = g['dates']
    rng = np.random.default_rng(SEED + 2026)

    tr = pd.read_parquet(os.path.join(DATA, 'hrbp_trades.parquet'))
    tr = tr[tr['entry_idx'] >= 0].reset_index(drop=True)
    dv = {a: tr[tr['arm'] == a].reset_index(drop=True)
          for a in tr['arm'].unique()}

    # ---- Null 1/2 : already produced by the signal engine ---------------
    lg('Null 1  = arm A_RANDOM        (random event, same stock+year)')
    lg('Null 2  = arm B_HVE_RANDOM    (HVE anchor, random entry in window)')
    rows = []
    for a, b, tag in (('B_HVE_RANDOM', 'A_RANDOM', 'Null1 B vs A'),
                      ('D_PRIMARY', 'A_RANDOM', 'Null1 D vs A'),
                      ('D_PRIMARY', 'B_HVE_RANDOM', 'Null2 D vs B')):
        rows += pair_block(lg, tag, dv[a], dv[b], rng)

    # ---- Null 3 : same stock + same month, outside the event window -----
    lg.sep('-')
    lg('Null 3  same stock + same calendar month, random eligible entry')
    amap = matched_key_index(g, el, N)
    lg('  matched index %d sessions' % len(amap['key']))
    n3 = []
    for a in ('C_BAND_R1', 'D_PRIMARY'):
        d = dv[a]
        si = d['s_i'].to_numpy().astype('int64')
        t0 = d['t0'].to_numpy().astype('int64')
        mon = d['month'].to_numpy().astype('int64')
        ent = d['entry_idx'].to_numpy().astype('int64')
        keys = si * 10000 + mon
        # same stock, same calendar month, same holding horizon; only the
        # observed fill day itself (and 5 sessions around it) is barred, so
        # the null may sit before OR after the event.
        f_lo, f_hi = ent - 5, ent + 5
        de = pick_matched(amap, keys, f_lo, f_hi, rng)
        ne = np.where(de >= 0, de + 1, -1)
        rec = {'arm': a, 's_i': si, 't0': t0, 'month': mon, 'null_entry': ne}
        for h in HOR:
            rec['r%d' % h] = ret_from_open(g, si, ne, h).astype('float64')
        nd = pd.DataFrame(rec)
        sub = d[['s_i', 't0', 'month']].copy()
        for h in HOR:
            sub['o%d' % h] = d['r%d' % h].to_numpy()
        mm = sub.merge(nd, on=['s_i', 't0', 'month'], how='inner')
        lg('  %s paired %d / %d' % (a, len(mm), len(d)))
        for h in HOR:
            ro = mm['o%d' % h].to_numpy()
            rn = mm['r%d' % h].to_numpy()
            both = np.isfinite(ro) & np.isfinite(rn)
            obs, lo, hi = cluster_boot_diff(
                ro[both], mm['month'].to_numpy()[both],
                rn[both], mm['month'].to_numpy()[both], B=B, seed=SEED)
            n3.append({'family': 'Null3 stock+month', 'observed_arm': a,
                       'null_arm': 'RANDOM_SAME_STOCK_MONTH',
                       'horizon': 'T+%d' % h, 'n_paired': int(both.sum()),
                       'obs_mean': float(np.mean(ro[both])),
                       'obs_net30': float(np.mean(ro[both]) - C30 / 1e4),
                       'null_mean': float(np.mean(rn[both])),
                       'null_median': float(np.median(rn[both])),
                       'excess': obs, 'ci_lo': lo, 'ci_hi': hi})
            r = n3[-1]
            lg('    %-10s %-5s n=%6d obs %+.4f null %+.4f excess %+.4f '
               '[%+.4f, %+.4f]' % (a, r['horizon'], r['n_paired'],
                                   r['obs_mean'], r['null_mean'], r['excess'],
                                   r['ci_lo'], r['ci_hi']))
    rows += n3
    save_csv(pd.DataFrame(rows), 'H_RBP_01_NULL_MODEL.csv')
    lg('H_RBP_01_NULL_MODEL.csv  %d rows' % len(rows))

    # ---- counterfactuals -------------------------------------------------
    lg.sep('=')
    lg('counterfactuals (paired on the same events)')
    cf = []
    cf += pair_block(lg, 'CF-1 same-event window', dv['D_PRIMARY'],
                     dv['B_HVE_RANDOM'], rng)
    cf += pair_block(lg, 'CF-2 after-touch', dv['D_PRIMARY'],
                     dv['CF2_RAND_AFTER_RETR'], rng)
    save_csv(pd.DataFrame(cf), '24_hrbp_counterfactual.csv')
    lg('24_hrbp_counterfactual.csv  %d rows' % len(cf))

    save_json({'null1': 'A_RANDOM random event, same stock+year',
               'null2': 'B_HVE_RANDOM uniform entry inside [t0+1,t0+60]',
               'null3': 'random eligible session in the SAME stock and the '
                        'SAME calendar month as the observed fill, excluding '
                        'the fill day +-5 sessions; holding horizon preserved',
               'cf1': 'D_PRIMARY vs B_HVE_RANDOM on the same events',
               'cf2': 'D_PRIMARY vs CF2_RAND_AFTER_RETR on the same events',
               'boot_B': B, 'seed': SEED},
              '24_hrbp_null_meta.json')
    lg('done')


if __name__ == '__main__':
    main()
