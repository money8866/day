# -*- coding: utf-8 -*-
"""H-MCE-01 read-only reconnaissance probe (no study code, no artefacts)."""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
HVE = os.path.join(ROOT, 'research', 'hve')
if HVE not in sys.path:
    sys.path.insert(0, HVE)

import hve_common as H   # noqa: E402

g = H.build_grid()
dates = g['dates']
el = H.eligibility(g)
N = len(dates)
print('panel S=%d N=%d  %s..%s' % (g['close'].shape[0], N, dates[0], dates[-1]))
print('PREREG.study_start=%s  min_list_days=%s  long_suspension_days=%s'
      % (H.PREREG['study_start'], H.PREREG['min_list_days'],
         H.PREREG['long_suspension_days']))

elig_cnt = el.sum(axis=0)
for y in range(2018, 2027):
    m = np.array([d[:4] == str(y) for d in dates])
    if not m.any():
        continue
    print('  %d  sessions=%3d  eligible_cells=%9d  eligible_days=%3d  '
          'first_elig_idx=%s'
          % (y, int(m.sum()), int(elig_cnt[m].sum()), int((elig_cnt[m] > 0).sum()),
             dates[int(np.argmax(elig_cnt > 0))] if (elig_cnt > 0).any() else 'NA'))

print('first session with any eligible cell: %s (idx %d)'
      % (dates[int(np.argmax(elig_cnt > 0))], int(np.argmax(elig_cnt > 0))))

# board mix
b = g['board']
import collections
print('board mix:', dict(collections.Counter(b.tolist())))
print('gone codes: %d  last_idx range %d..%d'
      % (int(np.asarray(g['gone']).sum()), int(g['last_idx'].min()),
         int(g['last_idx'].max())))

# total_mv availability
import pandas as pd
bp = pd.read_parquet(os.path.join(H.FS_DATA, 'basic_panel.parquet'),
                     columns=['ts_code', 'trade_date', 'total_mv'])
bp['trade_date'] = bp['trade_date'].astype(str)
print('basic_panel total_mv rows=%d  finite=%d  date range %s..%s'
      % (len(bp), int(np.isfinite(pd.to_numeric(bp['total_mv'],
                                                errors='coerce')).sum()),
         bp['trade_date'].min(), bp['trade_date'].max()))

# index panel
ip = pd.read_parquet(os.path.join(H.FS_DATA, 'index_panel.parquet'),
                     columns=['ts_code', 'trade_date', 'close'])
print('index ts_code sample:', sorted(set(ip['ts_code'].astype(str)))[:8],
      '... n=%d' % ip['ts_code'].nunique())
