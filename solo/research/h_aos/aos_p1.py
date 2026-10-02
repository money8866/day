# -*- coding: utf-8 -*-
"""H-AOS-01 P1 -- is there an Alpha x Regime dependence at all?

Priority P1 of the task text: before any router can be justified we must show
that the SAME alpha behaves differently across market regimes.  Nothing here
selects anything; P1 only measures.

Outputs (data/):
    aos_regime_series.csv          day-by-day regime label + features + locked thresholds
    aos_regime_context.csv         descriptive market state per regime
    aos_regime_phase_mix.csv       regime share inside IS / VALID / OOS
    aos_alpha_regime_matrix.csv    the SPEC section 7 core matrix
    aos_dependence_test.csv        Q1 permutation test
    aos_p1_results.json            machine-readable summary
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aos_common as A          # noqa: E402

B_PERM = 1000


def _marginal(core):
    """Unconditional per-alpha ledger (the solid baseline every cell is read against)."""
    rows = []
    for a in sorted(core['alpha'].unique()):
        s = core[core['alpha'] == a]
        d = A.cell_stats(s['ret'].to_numpy(), s['month'].to_numpy())
        d.update({'alpha': a, 'alpha_name': A.ALPHA_NAME.get(a, a)})
        rows.append(d)
    cols = ['alpha', 'alpha_name', 'n', 'win_rate', 'mean', 'median', 'pf', 't',
            'sharpe', 'mdd', 'worst_month', 'mean_net15', 'mean_net30',
            'mean_net50', 'pf_net30', 'win_net30', 'p05', 'p95', 'n_month']
    return pd.DataFrame(rows)[cols]


def main():
    lg = A.Log('aos_p1')
    lg.sep('=')
    lg('H-AOS-01  P1  Alpha x Regime dependence')
    lg('SPEC sha256 %s' % A.sha256(A.SPEC)[:16])
    lg.sep('-')

    panel = A.load_panel(lg=lg)
    g, el = panel['g'], panel['el']
    lab, feat, thr = A.regime_series(g, el, lg=lg)
    A.write_regime_series(lab, g['dates'], feat, thr)
    lg('wrote aos_regime_series.csv')

    ctx = A.regime_market_context(feat, lab, g['dates'])
    A.save_csv(ctx, 'aos_regime_context.csv')
    mix = A.regime_phase_mix(lab, g['dates'])
    A.save_csv(mix, 'aos_regime_phase_mix.csv')
    lg.sep('-')
    lg('regime context:')
    lg(ctx[['regime', 'n_days', 'share', 'mean_fwd1', 'mean_r20', 'mean_vol20',
            'mean_breadth', 'mean_ln_dn', 'below_min_share']].to_string(index=False))
    lg('regime phase mix:')
    lg(mix[['phase', 'n_days'] + [s + '_share' for s in A.RSTATES]]
       .to_string(index=False))
    lg.sep('-')

    core, ext, ext_meta = A.build_alphas(panel, lg=lg)
    core = A.attach_regime(core, lab, g['dates'])
    core['phase'] = [A.phase_of(x) for x in core['entry_date']]
    lg('core trades %d' % len(core))
    lg('trades by alpha x phase:')
    lg(pd.crosstab(core['alpha'], core['phase']).to_string())
    lg.sep('-')

    marg = _marginal(core)
    A.save_csv(marg, 'aos_alpha_marginal.csv')
    lg('marginal (unconditional) per alpha:')
    lg(marg[['alpha', 'n', 'win_rate', 'mean', 'mean_net30', 'pf_net30',
             'sharpe', 'mdd']].to_string(index=False))
    lg.sep('-')

    m = A.regime_matrix(core, lg=lg)
    A.save_csv(m, 'aos_alpha_regime_matrix.csv')
    lg('wrote aos_alpha_regime_matrix.csv')
    piv = m.pivot_table(index='alpha', columns='regime', values='mean_net30')
    npiv = m.pivot_table(index='alpha', columns='regime', values='n')
    lg('matrix mean_net30 (rows=alpha):')
    lg(piv.to_string())
    lg('matrix n:')
    lg(npiv.to_string())
    lg.sep('-')

    dep = A.dependence_test(core, B=B_PERM, lg=lg)
    A.save_csv(dep, 'aos_dependence_test.csv')
    lg('wrote aos_dependence_test.csv')
    lg(dep[['alpha', 'n_trades', 'spread_net30', 'null_mean', 'null_p95',
            'p_perm', 'significant_5pct']].to_string(index=False))

    # ---- P1 verdict -------------------------------------------------
    n_sig = int(dep['significant_5pct'].sum())
    n_tot = int(len(dep))
    res = {
        'step': 'P1',
        'spec_sha256': A.sha256(A.SPEC),
        'regime_thresholds': {k: (float(v) if not isinstance(v, list) else v)
                              for k, v in thr.items()},
        'regime_mix_full_panel': {s: int((lab == s).sum()) for s in A.RSTATES},
        'regime_shares_oos': {r['phase']: {s: r[s + '_share'] for s in A.RSTATES}
                              for _, r in mix.iterrows()},
        'core_trades': int(len(core)),
        'trades_by_alpha_phase': pd.crosstab(core['alpha'],
                                             core['phase']).to_dict(),
        'cells_total': int(len(m)),
        'cells_n_ge_min': int((m['n'] >= A.PREREG_AOS['min_cell_n']).sum()),
        'dependence': dep.to_dict(orient='records'),
        'n_alpha_significant_5pct': n_sig,
        'n_alpha_tested': n_tot,
        'P1_verdict': ('REGIME_DEPENDENCE_PRESENT' if n_sig == n_tot
                       else ('PARTIAL' if n_sig > 0 else 'NO_DEPENDENCE')),
        'ext_meta': ext_meta,
    }
    A.save_json(res, 'aos_p1_results.json')
    lg.sep('-')
    lg('P1 verdict: %s  (%d/%d alphas significant at 5%%)'
       % (res['P1_verdict'], n_sig, n_tot))
    lg('aos_p1 done')


if __name__ == '__main__':
    main()
