# -*- coding: utf-8 -*-
"""Throw-away probe 3: exact column semantics of the two production ledgers.
Read-only."""
import os
import pandas as pd

RD = r'd:\mystock\solo\report_daily'
pd.set_option('display.width', 250)
pd.set_option('display.max_rows', 200)
pd.set_option('display.max_columns', 200)


def cols(fn):
    p = os.path.join(RD, fn)
    d = pd.read_csv(p, low_memory=False)
    print('=' * 90)
    print(fn, d.shape)
    for i, c in enumerate(d.columns):
        print('   %2d  %-24s %-8s %s' % (i, c, str(d[c].dtype),
                                         str(d[c].iloc[0])[:40]))
    return d


h = cols('te_backtest_events_20250101_20260828.csv')
print('\n--- HVT: sample of unique signal_date / event_date / trade_date')
for c in h.columns:
    if 'date' in c.lower() or c in ('t0', 't0_date', 'event'):
        try:
            print('  %-24s n_uniq %-7d min %-12s max %-12s'
                  % (c, h[c].nunique(), str(h[c].min()), str(h[c].max())))
        except Exception as e:
            print('  %-24s ERR %s' % (c, e))
print('\n--- HVT head of 6 cols')
print(h[['ts_code', 'signal_date', 'decision_lag', 'next_day_action',
         'actual_entry', 'r10']].head(12).to_string())

w = cols('te3_v31_events_20240101_20260828.csv')
print('\n--- W7: date-ish columns')
for c in w.columns:
    if 'date' in c.lower() or c in ('t0', 'bar'):
        try:
            print('  %-24s n_uniq %-7d min %-12s max %-12s'
                  % (c, w[c].nunique(), str(w[c].min()), str(w[c].max())))
        except Exception as e:
            print('  %-24s ERR %s' % (c, e))
print('\n--- W7 head of key cols')
print(w[['code', 'event_date', 'signal_date', 'action', 'state',
         'r10']].head(12).to_string())
