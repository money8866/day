# -*- coding: utf-8 -*-
"""Throw-away probe 6: r* vs er*, and what actual_entry really is."""
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
cal = {d: i for i, d in enumerate(dates)}
hold = {c: i for i, c in enumerate(g['codes'])}

d = pd.read_csv(os.path.join(RD, 'te_backtest_events_20250101_20260828.csv'),
                low_memory=False)
a = d[d['next_day_action'].isin(['BUY', 'BUY_ON_CONFIRM'])].copy()
a['di'] = a['decision_date'].astype(str).map(cal)
a['si'] = a['ts_code'].astype(str).map(hold)
a = a[a['di'].notna() & a['si'].notna()]
si = a['si'].to_numpy().astype('int64')
di = a['di'].to_numpy().astype('int64')
N = g['close'].shape[1]

print('=== HVT actionable: r vs er, by lag')
for lag in (0, 1, 3, 10, 40):
    s = a[a['decision_lag'] == lag]
    if len(s) == 0:
        continue
    i = s.index
    print('  lag %2d n=%4d  r10 mean %+.3f  er10 mean %+.3f  '
          'frac(|r-er|>0.01) %.2f'
          % (lag, len(s), s['r10'].mean(), s['er10'].mean(),
             float((s['r10'] - s['er10']).abs().gt(0.01).mean())))

print('\n=== actual_entry vs panel prices')
ae = a['actual_entry'].to_numpy()
close_d = g['close'][si, np.clip(di, 0, N - 1)]
open_d1 = g['open'][si, np.clip(di + 1, 0, N - 1)]
raw = pd.read_parquet(
    r'd:\mystock\solo\research\fundamental_surprise_alpha\data\price_panel.parquet',
    columns=['ts_code', 'trade_date', 'close', 'open'])


def raw_at(codes, days, col):
    df = pd.DataFrame({'ts_code': codes, 'trade_date': days})
    return df.merge(raw[['ts_code', 'trade_date', col]],
                    on=['ts_code', 'trade_date'], how='left')[col].to_numpy()


codes = g['codes'][si]
day_d = np.array([dates[i] for i in di])
day_d1 = np.array([dates[i] if i < len(dates) else '' for i in di + 1])
r_open1 = raw_at(codes, day_d1, 'open')
r_close_d = raw_at(codes, day_d, 'close')
for nm, cand in (('raw open[d+1]', r_open1), ('raw close[d]', r_close_d)):
    ok = np.isfinite(cand) & np.isfinite(ae)
    dif = np.abs(cand[ok] - ae[ok]) / np.maximum(np.abs(ae[ok]), 1e-9)
    print('  %-16s n=%4d  match(rel<0.2%%) %5.1f%%  mean rel|diff| %.4f'
          % (nm, int(ok.sum()), 100.0 * (dif <= 0.002).mean(), dif.mean()))

print('\n=== er10 vs entry-based recompute (T+1 open, hold 10)')
mine = (g['close'][si, np.clip(di + 11, 0, N - 1)] / open_d1 - 1.0) * 100.0
led = a['er10'].to_numpy()
ok = np.isfinite(mine) & np.isfinite(led)
print('   n=%d corr %.4f mean|diff| %.3f  <=0.05pp %.1f%%'
      % (int(ok.sum()), np.corrcoef(mine[ok], led[ok])[0, 1],
         np.abs(mine[ok] - led[ok]).mean(),
         100.0 * (np.abs(mine[ok] - led[ok]) <= 0.05).mean()))
