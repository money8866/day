# -*- coding: utf-8 -*-
"""H-AOS-01 P2 -- can the regime be identified out-of-sample at all?

Priority P2: P1 may show that alphas differ across regimes, but that is only
useful if the CURRENT regime carries information about the FUTURE.  P2 tests
the detector itself:

  1. Stability under the pre-registered perturbations (SPEC section 11):
     thresholds x {0.8,1.0,1.2} x MA pairs {(20,60),(30,60),(20,30)}.
  2. Persistence / whipsaw / transition structure of the frozen detector.
  3. Does the regime label at t survive to t+H?  (the state must be a state)
  4. Q3: the IS regime -> alpha ranking applied to OOS (information, not fit).
  5. IS-vs-OOS rank agreement of the Alpha x Regime cells.

Outputs (data/):
    aos_parameter_stability.csv   stability grid (block='regime_threshold')
    aos_regime_transitions.csv    transition count matrix
    aos_regime_persistence.csv    label survival at several horizons
    aos_p2_results.json
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aos_common as A          # noqa: E402

HORIZONS = (1, 5, 10, 20)


def transition_matrix(lab):
    st = list(A.RSTATES)
    idx = {s: i for i, s in enumerate(st)}
    M = np.zeros((len(st), len(st)), dtype=int)
    lab = np.asarray(lab)
    ok = np.array([x in idx for x in lab])
    x = lab[ok]
    for i in range(x.size - 1):
        M[idx[x[i]], idx[x[i + 1]]] += 1
    df = pd.DataFrame(M, index=st, columns=st)
    df.index.name = 'from'
    return df.reset_index()


def persistence(lab, horizons):
    lab = np.asarray(lab)
    rows = []
    for H in horizons:
        a, b = lab[:-H], lab[H:]
        m = (a != 'NA') & (b != 'NA')
        rows.append({'horizon': H, 'n_pairs': int(m.sum()),
                     'same_rate': float((a[m] == b[m]).mean()) if m.any() else np.nan})
    return pd.DataFrame(rows)


def is_oos_cell_agreement(core):
    """Spearman rank agreement of (alpha, regime) cells IS vs OOS."""
    t = core.copy()
    t['net'] = t['ret'].astype('float64') - A.COST_PRIMARY / 10000.0
    g = (t.groupby(['alpha', 'regime', 'phase'])['net']
         .agg(['mean', 'size']).reset_index())
    g = g[g['size'] >= A.PREREG_AOS['min_cell_n']]
    is_ = g[g['phase'] == 'IS'].set_index(['alpha', 'regime'])['mean']
    oo = g[g['phase'] == 'OOS'].set_index(['alpha', 'regime'])['mean']
    j = pd.concat({'IS': is_, 'OOS': oo}, axis=1, join='inner').dropna()
    if len(j) >= 4:
        rho = float(j['IS'].corr(j['OOS'], method='spearman'))
        pear = float(j['IS'].corr(j['OOS']))
    else:
        rho = pear = np.nan
    return j, rho, pear


def main():
    lg = A.Log('aos_p2')
    lg.sep('=')
    lg('H-AOS-01  P2  Regime identifiability / OOS information')
    lg('SPEC sha256 %s' % A.sha256(A.SPEC)[:16])
    lg.sep('-')

    panel = A.load_panel(lg=lg)
    g, el = panel['g'], panel['el']
    lab, feat, thr = A.regime_series(g, el, lg=lg)
    dates = g['dates']
    lg.sep('-')

    # ---- 1. stability grid ------------------------------------------
    st, base_rs, weak = A.regime_stability(feat, dates, thr, lg=lg)
    st['block'] = 'regime_threshold'
    st['is_baseline'] = (st['thr_scale'] == 1.0) & (st['ma_fast'] == 20) & (st['ma_slow'] == 60)
    A.save_csv(st, 'aos_parameter_stability.csv')
    lg('wrote aos_parameter_stability.csv (regime_threshold block)')
    lg.sep('-')

    # ---- 2. persistence / transitions --------------------------------
    per = persistence(lab, HORIZONS)
    A.save_csv(per, 'aos_regime_persistence.csv')
    lg('label persistence:')
    lg(per.to_string(index=False))
    tr = transition_matrix(lab)
    A.save_csv(tr, 'aos_regime_transitions.csv')
    lg('transition matrix (from -> to):')
    lg(tr.to_string(index=False))

    okv = np.asarray(lab) != 'NA'
    ph = np.array([A.phase_of(d) for d in dates])
    rs_rows = []
    for name, m in (('ALL', okv), ('IS', okv & (ph == 'IS')),
                    ('VALID', okv & (ph == 'VALID')), ('OOS', okv & (ph == 'OOS'))):
        r = A.run_stats(lab, valid=m)
        r['window'] = name
        rs_rows.append(r)
    rs = pd.DataFrame(rs_rows)
    lg('run stats by window:')
    lg(rs.to_string(index=False))
    lg.sep('-')

    # ---- 3. Q3 OOS information ---------------------------------------
    core, ext, ext_meta = A.build_alphas(panel, lg=lg)
    core = A.attach_regime(core, lab, dates)
    core['phase'] = [A.phase_of(x) for x in core['entry_date']]
    q3 = A.regime_oos_information(core, lab, dates, B=1000, lg=lg)
    lg.sep('-')

    # ---- 4. IS vs OOS cell agreement ---------------------------------
    j, rho, pear = is_oos_cell_agreement(core)
    A.save_csv(j.reset_index(), 'aos_cell_is_oos.csv')
    lg('IS-vs-OOS cell agreement: n_cells=%d  spearman=%s  pearson=%s'
       % (len(j), A.fmt(rho), A.fmt(pear)))
    if len(j):
        lg(j.to_string())
    lg.sep('-')

    verdict = ('REGIME_IDENTIFIABLE' if (not weak and q3['oos_information'])
               else ('WEAK_DETECTOR' if weak and not q3['oos_information']
                     else 'PARTIAL'))
    res = {
        'step': 'P2',
        'spec_sha256': A.sha256(A.SPEC),
        'stability': st.to_dict(orient='records'),
        'baseline_run_stats': base_rs,
        'stability_verdict': 'WEAK' if weak else 'STABLE',
        'persistence': per.to_dict(orient='records'),
        'run_stats_by_window': rs.to_dict(orient='records'),
        'q3_oos_information': q3,
        'is_oos_cell_agreement': {'n_cells': int(len(j)),
                                  'spearman': rho, 'pearson': pear},
        'P2_verdict': verdict,
    }
    A.save_json(res, 'aos_p2_results.json')
    lg('P2 verdict: %s' % verdict)
    lg('aos_p2 done')


if __name__ == '__main__':
    main()
