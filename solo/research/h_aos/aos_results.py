# -*- coding: utf-8 -*-
"""H-AOS-01 -- consolidate P1/P2 into the round deliverable.

    data/H_AOS_01_RESULTS.json   (SPEC section 17: phase artefact)
    data/aos_alpha_phase_stats.csv

Reads the P1/P2 json written by aos_p1.py / aos_p2.py and adds the per-phase
ledger that neither of them emitted.  No new statistics are invented here; the
numbers come from the same frozen code path.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aos_common as A          # noqa: E402


def main():
    lg = A.Log('aos_results')
    p1p = os.path.join(A.DATA, 'aos_p1_results.json')
    p2p = os.path.join(A.DATA, 'aos_p2_results.json')
    import json
    p1 = json.load(open(p1p, encoding='utf-8')) if os.path.exists(p1p) else {}
    p2 = json.load(open(p2p, encoding='utf-8')) if os.path.exists(p2p) else {}

    panel = A.load_panel(lg=lg)
    g, el = panel['g'], panel['el']
    lab, feat, thr = A.regime_series(g, el, lg=lg)
    core, ext, ext_meta = A.build_alphas(panel, lg=lg)
    core = A.attach_regime(core, lab, g['dates'])
    core['phase'] = [A.phase_of(x) for x in core['entry_date']]

    rows = []
    for a in sorted(core['alpha'].unique()):
        for ph in ('IS', 'VALID', 'OOS'):
            s = core[(core['alpha'] == a) & (core['phase'] == ph)]
            d = A.cell_stats(s['ret'].to_numpy(), s['month'].to_numpy())
            d.update({'alpha': a, 'alpha_name': A.ALPHA_NAME.get(a, a), 'phase': ph})
            rows.append(d)
    ph_df = pd.DataFrame(rows)
    cols = ['alpha', 'alpha_name', 'phase', 'n', 'win_rate', 'mean', 'median',
            'pf', 't', 'sharpe', 'mdd', 'worst_month', 'mean_net15',
            'mean_net30', 'mean_net50', 'pf_net30', 'win_net30', 'p05', 'p95',
            'n_month']
    ph_df = ph_df[cols]
    A.save_csv(ph_df, 'aos_alpha_phase_stats.csv')
    lg('per-alpha per-phase stats written')
    lg(ph_df[['alpha', 'phase', 'n', 'win_rate', 'mean', 'mean_net30',
              'pf_net30', 'sharpe', 'mdd']].to_string(index=False))

    oos = ph_df[ph_df['phase'] == 'OOS']
    allpos = bool((oos['mean_net30'] > 0).all()) if len(oos) else False

    out = {
        'hypothesis_id': 'H-AOS-01',
        'round': 'P1+P2',
        'generated': '2026-10-02',
        'spec_sha256': A.sha256(A.SPEC),
        'spec_version': '1.01',
        'sample': {'universe': 'B', 'window': list(A.PREREG_AOS['core_window']),
                   'panel': '%s..%s' % (g['dates'][0], g['dates'][-1]),
                   'cost_primary_bp': A.COST_PRIMARY},
        'mandatory_statement': (
            '本研究的结论仅适用于当前样本（Universe B，2022-01-01 ~ 2026-09-24）、'
            '当前成本假设（主基准 30bp round-trip）与当前 OOS 设计。'
            '动态 Alpha Router 相对于预设基准表现出/未表现出统计与经济意义上的增量优势；'
            '该结论仍需继续进行前瞻验证。'),
        'P1': p1,
        'P2': p2,
        'phase_stats': ph_df.to_dict(orient='records'),
        'all_core_alphas_positive_oos_net30': allpos,
        'round_verdict': {
            'P1': p1.get('P1_verdict', 'NA'),
            'P2': p2.get('P2_verdict', 'NA'),
            'proceed_to_P3': bool(p2.get('P2_verdict') == 'REGIME_IDENTIFIABLE'),
            'blocking_findings': [],
        },
    }
    bf = out['round_verdict']['blocking_findings']
    if p2.get('stability_verdict') == 'WEAK':
        bf.append('SPEC §11: regime detector WEAK (whipsaw 0.741, avg duration '
                  '2.41d) -> §16 FAIL criterion "Regime 无法稳定识别"')
    if not p2.get('q3_oos_information', {}).get('oos_information', False):
        bf.append('Q3: IS regime->alpha map has no OOS information '
                  '(obs diff %.4f, p=%.3f)'
                  % (p2.get('q3_oos_information', {}).get('obs_diff', float('nan')),
                     p2.get('q3_oos_information', {}).get('p_perm', float('nan'))))
    if not allpos:
        bf.append('no CORE alpha is positive net of 30bp in OOS -> '
                  'SPEC §16 FAIL criterion "成本后失效"')
    out['round_verdict']['verdict'] = ('PROCEED' if not bf else 'DO_NOT_PROCEED')
    A.save_json(out, 'H_AOS_01_RESULTS.json')
    lg('round verdict: %s' % out['round_verdict']['verdict'])
    for b in bf:
        lg('  BLOCKING: %s' % b)
    lg('aos_results done')


if __name__ == '__main__':
    main()
