# -*- coding: utf-8 -*-
"""Smoke test for has_common: panels, ledgers, buckets, matcher."""
import numpy as np
import pandas as pd

import has_common as C

lg = C.Log('_smoke')
g = C.grid(lg=lg)
lg('keys %s' % sorted(g.keys()))
S, N = g['close'].shape
lg('S=%d N=%d  %s..%s' % (S, N, g['dates'][0], g['dates'][-1]))

el = C.eligibility(lg=lg)
mv = C.mv_panel(g, lg=lg)
lq = C.liq_panel(g, lg=lg)
ind, ids = C.industry_panel(g, lg=lg)

mv_b = C.tercile_ids(el, np.log(np.where(mv > 0, mv, np.nan)), 3, lg=lg, tag='mv')
lq_b = C.tercile_ids(el, np.log(np.where(lq > 0, lq, np.nan)), 3, lg=lg, tag='liq')
board_id = np.array([C.BOARD_ID.get(b, 0) for b in g['board']], dtype='int64')
lg('board ids %s' % np.bincount(board_id).tolist())

obs = C.load_obs(lg=lg)
lg('obs total %d' % len(obs))
lg(obs.groupby(['system', 'year']).size().to_string())
for sysname in ('HVT', 'W7'):
    s = obs[obs['system'] == sysname]
    for h in C.PREREG_A['horizons']:
        r = C.ret_open(g, s['s_i'], s['entry_idx'], h)
        ok = np.isfinite(r)
        lg('  %s T+%-3d n=%5d mean %+8.4f  net30 %+8.4f  win %.1f%%'
           % (sysname, h, int(ok.sum()), np.nanmean(r),
              np.nanmean(C.cost_net(r)), 100.0 * np.nanmean(r[ok] > 0)))
    rc = C.ret_close_obs(g, s['s_i'], s['dec_idx'], 10)
    lg('  %s close-basis T+10 mean %+8.4f (ledger convention)'
       % (sysname, np.nanmean(rc)))

amap = C.strata_index(el, mv_b, lq_b, ind, board_id, lg=lg)
ban = (obs['dec_idx'].to_numpy() * S + obs['s_i'].to_numpy())
ban = np.sort(np.unique(ban))
keys = C.cell_key(obs['entry_idx'].to_numpy(),
                  board_id[obs['s_i'].to_numpy()],
                  mv_b[obs['s_i'].to_numpy(), obs['entry_idx'].to_numpy()],
                  lq_b[obs['s_i'].to_numpy(), obs['entry_idx'].to_numpy()])
want = ind[obs['s_i'].to_numpy(), obs['entry_idx'].to_numpy()]
rng = np.random.default_rng(C.PREREG_A['seed'])
ctrl, mode = C.pick_stratum(amap, keys, want, obs['s_i'].to_numpy(),
                            obs['entry_idx'].to_numpy(), ban, rng, S)
ok = ctrl >= 0
lg('matched control available %d / %d (%.1f%%)  exact-industry %.1f%%  '
   'coarsened %.1f%%'
   % (int(ok.sum()), len(obs), 100.0 * ok.mean(),
      100.0 * (mode == 0).mean(), 100.0 * (mode == 1).mean()))

mid = C.month_index(g, el, lg=lg)
lg('done')
