# -*- coding: utf-8 -*-
"""Throw-away probe 5: what exactly does the ledger's r* measure?

Validate the production ledger return columns against the research panel so
the counterfactual can be built on an identical price basis.  Read-only.
"""
import os
import sys

import numpy as np
import pandas as pd

HVE = r'd:\mystock\solo\research\hve'
if HVE not in sys.path:
    sys.path.insert(0, HVE)
import hve_common as H          # noqa: E402

RD = r'd:\mystock\solo\report_daily'
pd.set_option('display.width', 220)

g = H.build_grid(lg=None, use_cache=True)
dates = list(g['dates'])
hold = {c: i for i, c in enumerate(g['codes'])}
print('grid S=%d N=%d %s..%s' % (g['close'].shape + (dates[0], dates[-1])))


def probe(fn, code_col, day_col, act_filter, label):
    d = pd.read_csv(os.path.join(RD, fn), low_memory=False)
    d = d[act_filter(d)].copy()
    d['di'] = d[day_col].astype(str).map({x: i for i, x in enumerate(dates)})
    d['si'] = d[code_col].astype(str).map(hold)
    d = d[d['si'].notna() & d['di'].notna()]
    d = d.sample(min(1500, len(d)), random_state=7)
    si = d['si'].to_numpy().astype('int64')
    di = d['di'].to_numpy().astype('int64')
    print('\n=== %s   n_sample %d' % (label, len(d)))

    variants = {
        'C[d+1]/C[d]': ('close', 'close', 0, 1),
        'C[d+1]/O[d]': ('close', 'open', 0, 1),
        'C[d+2]/O[d+1]': ('close', 'open', 1, 2),
    }
    for name, v in variants.items():
        f1, f2, k1, k2 = v
        a = g[f2][si, np.clip(di + k1, 0, g['close'].shape[1] - 1)]
        b = g[f1][si, np.clip(di + k2, 0, g['close'].shape[1] - 1)]
        mine = (b / a - 1.0) * 100.0
        led = d['r1'].to_numpy()
        ok = np.isfinite(mine) & np.isfinite(led)
        if ok.sum() < 10:
            print('   %-16s n=%d  -- too few' % (name, int(ok.sum())))
            continue
        dif = np.abs(mine[ok] - led[ok])
        print('   %-16s n=%4d  corr %.4f  mean|diff| %.3f  <=0.05pp %5.1f%%'
              % (name, int(ok.sum()), np.corrcoef(mine[ok], led[ok])[0, 1],
                 dif.mean(), 100.0 * (dif <= 0.05).mean()))

    # also multi-horizon for the best variant guess
    for h, k in ((3, 3), (5, 5), (10, 10), (20, 20)):
        a = g['close'][si, np.clip(di, 0, g['close'].shape[1] - 1)]
        b = g['close'][si, np.clip(di + k, 0, g['close'].shape[1] - 1)]
        mine = (b / a - 1.0) * 100.0
        led = d['r%d' % h].to_numpy()
        ok = np.isfinite(mine) & np.isfinite(led)
        dif = np.abs(mine[ok] - led[ok])
        print('   C[d+%d]/C[d]      n=%4d  corr %.4f  mean|diff| %.3f  '
              '<=0.05pp %5.1f%%' % (h, int(ok.sum()),
                                    np.corrcoef(mine[ok], led[ok])[0, 1],
                                    dif.mean(), 100.0 * (dif <= 0.05).mean()))
    print('   ledger r10 mean %+.3f   panel-based mean above'
          % d['r10'].mean())


probe('te_backtest_events_20250101_20260828.csv', 'ts_code', 'decision_date',
      lambda d: d['next_day_action'].isin(['BUY', 'BUY_ON_CONFIRM']),
      'HVT actionable (decision_date)')

probe('te_backtest_events_20250101_20260828.csv', 'ts_code', 'signal_date',
      lambda d: d['next_day_action'].isin(['BUY', 'BUY_ON_CONFIRM']),
      'HVT actionable (signal_date)')

probe('te3_v31_events_20240101_20260828.csv', 'code', 'signal_date',
      lambda d: d['action'].isin(['CONDITIONAL BUY', 'PRIMARY BUY']),
      'W7 actionable (signal_date)')
