# -*- coding: utf-8 -*-
"""H-RBP-01 step 6 -- walk-forward selection and the frozen-cell reference.

Folds (rolling, pre-registered):
    train 3 years, validation 1 year, test 1 year, step 1 year, 2019..2026
    fold 1  train 2019-21  valid 2022  test 2023
    ...
    fold 4  train 2022-24  valid 2025  test 2026

Inside a fold the cell is chosen on the TRAIN window alone (argmax of the
train net-30 mean among cells with enough events), its sign is *checked* on
validation, and the number that counts is the TEST window -- which is never
touched by the choice.  The frozen, pre-registered arms are reported next to
it, so the reader can see how much of any test performance is selection.
"""
import os

import numpy as np
import pandas as pd

from hrbp_common import PREREG_H, DATA, Log, save_csv, save_json

PH = PREREG_H['primary_horizon']
MIN_CELL = PREREG_H['min_cell_events']
TR = PREREG_H['wf_train_years']
VA = PREREG_H['wf_valid_years']
TE = PREREG_H['wf_test_years']
Y0 = PREREG_H['wf_start_year']
Y1 = PREREG_H['wf_end_year']
C30 = PREREG_H['primary_cost_bp']


def wmean(df, y):
    s = df[df['year'].isin(y)]
    n = float(s['n'].sum())
    if n <= 0:
        return np.nan, 0.0
    return float((s['mean_r10_net30'] * s['n']).sum() / n), n


def main():
    lg = Log('hrbp_wf')
    lg('H-RBP-01 walk-forward')
    lg.sep('=')

    gr = pd.read_csv(os.path.join(DATA, '12_hrbp_grid_year.csv'))
    tr = pd.read_parquet(os.path.join(DATA, 'hrbp_trades.parquet'))
    tr = tr[tr['entry_idx'] >= 0]

    folds = []
    y = Y0
    while y + TR + VA + TE - 1 <= Y1:
        folds.append((list(range(y, y + TR)),
                      [y + TR + VA - 1],
                      [y + TR + VA]))
        y += 1
    rows = []
    for trn, val, tst in folds:
        # --- selection happens on train only ---------------------------
        trn_df = gr[gr['year'].isin(trn)]
        agg = trn_df.groupby(['thr_pct', 'struct', 'struct_at', 'rs'],
                             as_index=False).apply(
            lambda s: pd.Series({'n': s['n'].sum(),
                                 'net30': (s['mean_r10_net30']
                                           * s['n']).sum() / s['n'].sum()}))
        agg = agg[agg['n'] >= MIN_CELL].reset_index(drop=True)
        if len(agg) == 0:
            continue
        sel = agg.loc[agg['net30'].idxmax()]
        cell = gr[(gr['thr_pct'] == sel['thr_pct']) &
                  (gr['struct'] == sel['struct']) &
                  (gr['struct_at'] == sel['struct_at']) &
                  (gr['rs'] == sel['rs'])]
        v_net, v_n = wmean(cell, val)
        t_net, t_n = wmean(cell, tst)
        t_win = float((cell[cell['year'].isin(tst)]['win30']
                       * cell[cell['year'].isin(tst)]['n']).sum()
                      / max(t_n, 1))

        # --- frozen reference: the pre-registered band primary ----------
        fr = []
        for a in ('A_RANDOM', 'B_HVE_RANDOM', 'C_BAND_R1', 'D_PRIMARY'):
            s = tr[(tr['arm'] == a) & (tr['year'].isin(tst))]
            r = s['r%d' % PH].to_numpy()
            r = r[np.isfinite(r)] - C30 / 1e4
            fr.append(float(r.mean()) if r.size else np.nan)
        # the threshold-family analogue of the primary cell (thr=3)
        ana = gr[(gr['thr_pct'] == 3) &
                 (gr['struct'] == PREREG_H['primary_cell']['struct']) &
                 (gr['struct_at'] == PREREG_H['primary_cell']['struct_at']) &
                 (gr['rs'] == PREREG_H['primary_cell']['rs'])]
        a_net, a_n = wmean(ana, tst)

        rows.append({'fold': len(rows) + 1,
                     'train': '%d-%d' % (trn[0], trn[-1]),
                     'valid': val[0], 'test': tst[0],
                     'n_cells': len(agg),
                     'sel_thr': int(sel['thr_pct']), 'sel_struct': sel['struct'],
                     'sel_at': sel['struct_at'], 'sel_rs': sel['rs'],
                     'train_net30': float(sel['net30']),
                     'valid_net30': v_net, 'valid_n': int(v_n),
                     'test_net30': t_net, 'test_n': int(t_n),
                     'test_win': t_win,
                     'frozen_A': fr[0], 'frozen_B': fr[1],
                     'frozen_C_R1': fr[2], 'frozen_D': fr[3],
                     'frozen_D_thr3': a_net})
        lg('fold%d train %d-%d -> sel thr=%d%% %s@%s/%s | valid %+.4f | '
           'TEST %+.4f (n=%d)  [frozen D %+.4f  A %+.4f]'
           % (rows[-1]['fold'], trn[0], trn[-1], rows[-1]['sel_thr'],
              rows[-1]['sel_struct'], rows[-1]['sel_at'], rows[-1]['sel_rs'],
              v_net, t_net, int(t_n), fr[3], fr[0]))

    wf = pd.DataFrame(rows)
    save_csv(wf, 'H_RBP_01_WALK_FORWARD.csv')
    lg.sep('-')
    if len(wf):
        lg('selected-on-train TEST mean %+.4f (median %+.4f), positive folds '
           '%d/%d' % (wf['test_net30'].mean(), wf['test_net30'].median(),
                      int((wf['test_net30'] > 0).sum()), len(wf)))
        lg('frozen D      TEST mean %+.4f, positive folds %d/%d'
           % (wf['frozen_D'].mean(), int((wf['frozen_D'] > 0).sum()), len(wf)))
        lg('frozen A null TEST mean %+.4f' % wf['frozen_A'].mean())
    lg.sep('-')
    lg('yearly net30 @T+10, frozen arms (from trades)')
    yr = []
    for a in ('A_RANDOM', 'B_HVE_RANDOM', 'C_BAND_R1', 'D_PRIMARY'):
        for y, s in tr[tr['arm'] == a].groupby('year'):
            r = s['r%d' % PH].to_numpy()
            r = r[np.isfinite(r)] - C30 / 1e4
            if r.size == 0:
                continue
            gp = float(r[r > 0].sum())
            gl = float(r[r <= 0].sum())
            yr.append({'arm': a, 'year': y, 'n': r.size, 'mean_net30': r.mean(),
                       'win_rate': float((r > 0).mean()),
                       'pf30': (gp / abs(gl)) if gl < 0 else np.inf})
    yd = pd.DataFrame(yr)
    save_csv(yd, '26_hrbp_year_arm.csv')
    piv = yd.pivot_table(index='year', columns='arm', values='mean_net30')
    keep = [c for c in ('A_RANDOM', 'B_HVE_RANDOM', 'C_BAND_R1', 'D_PRIMARY')
            if c in piv.columns]
    lg('%-6s %10s %10s %10s %10s' % tuple(['year'] + keep))
    for y, row in piv.iterrows():
        lg('%-6d %10.4f %10.4f %10.4f %10.4f'
           % tuple([y] + [row.get(c, np.nan) for c in keep]))
    lg('D_PRIMARY positive years %d/%d'
       % (int((piv['D_PRIMARY'] > 0).sum()), len(piv)))
    lg('C_BAND_R1 positive years %d/%d'
       % (int((piv['C_BAND_R1'] > 0).sum()), len(piv)))

    save_json({'folds': [{'train': r['train'], 'valid': r['valid'],
                          'test': r['test'],
                          'sel_thr': r['sel_thr'], 'sel_struct': r['sel_struct'],
                          'sel_at': r['sel_at'], 'sel_rs': r['sel_rs'],
                          'test_net30': r['test_net30'],
                          'frozen_D': r['frozen_D']} for r in rows],
               'selection_rule': 'argmax train net30 among cells with '
                                 'n >= %d, sign checked on validation, '
                                 'test untouched' % MIN_CELL,
               'note': 'the test window never feeds the choice'},
              '26_hrbp_wf_meta.json')
    lg('done')


if __name__ == '__main__':
    main()
