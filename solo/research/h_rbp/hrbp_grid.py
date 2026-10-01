# -*- coding: utf-8 -*-
"""H-RBP-01 step 5 -- parameter sensitivity matrix.

The grid is the pre-registered cross product

    threshold  6 (3,5,8,10,12,15 %)      x
    structure  3 (MA20, event-low, both) x
    structure_at 2 (at the touch, at the signal) x
    re-strength 3 (3-day high, 5-day high, MA20)   = 108 cells

Aggregated per calendar year by hrbp_signals.py.  This module pools them,
builds the four marginal profiles and applies the pre-registered
PARAMETER_FRAGILE test: a result that only exists in a single cell is not a
result.  NO cell is selected here.
"""
import os

import numpy as np
import pandas as pd

from hrbp_common import PREREG_H, DATA, Log, save_csv, save_json

PC = PREREG_H['primary_cell']
MIN_CELL = PREREG_H['min_cell_events']


def main():
    lg = Log('hrbp_grid')
    lg('H-RBP-01 parameter grid')
    lg.sep('=')

    src = os.path.join(DATA, '12_hrbp_grid_year.csv')
    df = pd.read_csv(src)
    lg('grid-year rows %d  cells %d' % (len(df), df['cell_id'].nunique()))

    # weighted means reconstructed from the per-year counts
    df['sum_r10'] = df['mean_r10'] * df['n']
    df['sum_net'] = df['mean_r10_net30'] * df['n']
    df['nwin'] = df['win30'] * df['n']
    g2 = df.groupby(['cell_id', 'thr_pct', 'struct', 'struct_at', 'rs'],
                    as_index=False).agg(
        n=('n', 'sum'), sum_r10=('sum_r10', 'sum'), sum_net=('sum_net', 'sum'),
        nwin=('nwin', 'sum'), years=('year', 'nunique'),
        ymin=('year', 'min'), ymax=('year', 'max'))
    g2['mean_r10'] = g2['sum_r10'] / g2['n']
    g2['mean_net30'] = g2['sum_net'] / g2['n']
    g2['win30'] = g2['nwin'] / g2['n']
    pos_years = df.assign(pos=(df['mean_r10_net30'] > 0).astype(int)) \
                  .groupby('cell_id')['pos'].sum()
    g2 = g2.merge(pos_years.rename('pos_years'), on='cell_id', how='left')
    g2 = g2[g2['n'] >= MIN_CELL].reset_index(drop=True)
    lg('cells with n >= %d : %d' % (MIN_CELL, len(g2)))

    g2.to_csv(os.path.join(DATA, 'H_RBP_01_PARAMETER_GRID.csv'),
              index=False, encoding='utf-8-sig')

    best = g2.loc[g2['mean_net30'].idxmax()]
    wst = g2.loc[g2['mean_net30'].idxmin()]
    pos_share = float((g2['mean_net30'] > 0).mean())
    lg('best cell  net30 %+.4f  n=%d  %s/%s@%s/%s' %
       (best['mean_net30'], best['n'], best['thr_pct'], best['struct'],
        best['struct_at'], best['rs']))
    lg('worst cell net30 %+.4f' % wst['mean_net30'])
    lg('share of cells with net30 > 0 : %.3f' % pos_share)

    # ---- primary cell sanity -------------------------------------------
    pc = g2[(g2['thr_pct'] == 3) & (g2['struct'] == PC['struct']) &
            (g2['struct_at'] == PC['struct_at']) & (g2['rs'] == PC['rs'])]
    if len(pc):
        r = pc.iloc[0]
        lg('primary cell check: n=%d net30 %+.4f  (D_PRIMARY arm must match)'
           % (r['n'], r['mean_net30']))

    # ---- marginal profiles ---------------------------------------------
    lg.sep('-')
    marg = {}
    for dim in ('thr_pct', 'struct', 'struct_at', 'rs'):
        m = g2.groupby(dim).apply(
            lambda s: pd.Series({'n_cells': len(s),
                                 'mean_net30': np.average(s['mean_net30'],
                                                          weights=s['n']),
                                 'min': s['mean_net30'].min(),
                                 'max': s['mean_net30'].max(),
                                 'share_pos': (s['mean_net30'] > 0).mean()}))
        marg[dim] = m
        lg('marginal over %s' % dim)
        for k, row in m.iterrows():
            lg('   %-24s cells %3d  wmean %+.4f  [%+.4f, %+.4f]  pos %.2f' %
               (str(k), int(row['n_cells']), row['mean_net30'], row['min'],
                row['max'], row['share_pos']))

    # ---- pre-registered fragility test ----------------------------------
    lg.sep('-')
    spike = bool(best['mean_net30'] > 0.005 and pos_share < 0.30
                 and best['pos_years'] <= 3)
    fragile = bool(best['mean_net30'] > 0 and pos_share < 0.50)
    verdict = ('PARAMETER_FRAGILE' if (spike or fragile) else 'PLATFORM')
    lg('spike=%s fragile=%s pos_share=%.3f best_pos_years=%d -> %s'
       % (spike, fragile, pos_share, best['pos_years'], verdict))

    save_json({'n_cells': int(len(g2)), 'min_cell_events': MIN_CELL,
               'best': {'net30': float(best['mean_net30']), 'n': int(best['n']),
                        'thr': int(best['thr_pct']), 'struct': best['struct'],
                        'struct_at': best['struct_at'], 'rs': best['rs'],
                        'pos_years': int(best['pos_years'])},
               'worst_net30': float(wst['mean_net30']),
               'share_cells_positive': pos_share,
               'spike': spike, 'fragile': fragile, 'verdict': verdict,
               'note': 'no cell is selected; the grid is reported whole'},
              '25_hrbp_grid_meta.json')

    # yearly view of the primary cell
    yp = df[(df['thr_pct'] == 3) & (df['struct'] == PC['struct']) &
            (df['struct_at'] == PC['struct_at']) & (df['rs'] == PC['rs'])]
    save_csv(yp, '25_hrbp_grid_primary_by_year.csv')
    lg('primary cell by year')
    for r in yp.to_dict('records'):
        lg('   %d n=%6d mean %+.4f net30 %+.4f win %.3f' %
           (r['year'], r['n'], r['mean_r10'], r['mean_r10_net30'], r['win30']))
    lg('done')


if __name__ == '__main__':
    main()
