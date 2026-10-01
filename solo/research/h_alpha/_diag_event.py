# -*- coding: utf-8 -*-
"""diagnostic: is the level-2 counterfactual contaminated by the pre-event run-up?

Two questions only.
  (a) where do the matched random dates land relative to the event, and what do
      the two sides contribute?
  (b) the event-time profile: mean forward return as a function of the offset
      from the event, plus the unconditional panel baseline.
No result from this file feeds back into the pre-registered design.
"""
import os
import numpy as np
import pandas as pd

import has_common as C

P = C.PREREG_A
OUT = os.path.join(C.OUT, 'data')


def main():
    lg = C.Log('diag_event')
    df = pd.read_csv(os.path.join(OUT, 'H_ALPHA_SOURCE_01_MATCHED.csv'),
                     low_memory=False)
    g = C.grid(lg=lg)
    S, N = g['close'].shape
    el = C.eligibility(lg=lg)
    si = df['s_i'].to_numpy('int64')
    evi = df['ev_idx'].to_numpy('int64')
    eii = df['entry_idx'].to_numpy('int64')
    rd = df['rdate_idx'].to_numpy('int64')
    H = 10

    lg.sep()
    lg('(a) where the matched random dates land, vs the event and the fill')
    ok = rd >= 0
    pre = ok & (rd < evi)
    post = ok & (rd >= evi)
    lg('  random date BEFORE the event  %5d (%.3f)  T+10 mean %+.4f'
       % (int(pre.sum()), pre.mean(),
          float(np.nanmean(df['r_rdate_%d' % H][pre]))))
    lg('  random date AFTER  the event  %5d (%.3f)  T+10 mean %+.4f'
       % (int(post.sum()), post.mean(),
          float(np.nanmean(df['r_rdate_%d' % H][post]))))
    lg('  event leg  open[ev+1]         %5d           T+10 mean %+.4f'
       % (int(ok.sum()), float(np.nanmean(df['r_ev_%d' % H][ok]))))
    lg('  system own fill               %5d           T+10 mean %+.4f'
       % (len(df), float(np.nanmean(df['r_obs_%d' % H]))))

    lg.sep()
    lg('(b) event-time profile: buy at open[ev + tau], hold %d sessions' % H)
    taus = [-30, -20, -15, -10, -7, -5, -3, -1, 0, 1, 3, 5, 7, 10, 15, 20, 30]
    for t in taus:
        r = C.ret_open(g, si, evi + t, H)
        lg('  tau=%+4d  n=%5d  mean %+.4f  median %+.4f  win %.3f'
           % (t, int(np.isfinite(r).sum()), float(np.nanmean(r)),
              float(np.nanmedian(r)), float(np.nanmean(r > 0))))
        _ = P
    lg.sep()
    lg('unconditional panel baseline: all eligible (stock, day) cells')
    si_e, di_e = np.nonzero(el)
    keep = di_e <= (N - 2 - H)
    si_e, di_e = si_e[keep], di_e[keep]
    sub = np.arange(0, si_e.size, max(1, si_e.size // 400000))
    r = C.ret_open(g, si_e[sub], di_e[sub], H)
    lg('  n=%d  mean %+.4f  median %+.4f  win %.3f'
       % (int(np.isfinite(r).sum()), float(np.nanmean(r)),
          float(np.nanmedian(r)), float(np.nanmean(r > 0))))
    lg('done')


if __name__ == '__main__':
    main()
