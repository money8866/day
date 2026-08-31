# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, 'd:/mystock/solo')
import pandas as pd
from hvt_bull.backtest import _tail_stat

df = pd.read_csv('report_daily/hvt_bull_backtest_events_20250101_20260828.csv')
m = df['breakout_realtime'].astype(str).eq('True')
m &= df['breakout'].astype(str).eq('True')
m &= ~df['false_breakout'].astype(str).eq('True')
x = df[m].copy()
cols = ['n', 'win', 'mean', 'median', 'p75', 'p90', 'max',
        'ge10', 'ge20', 'ge30', 'ge50', 'top10_avg', 'top5_avg']
print('state | ' + ' | '.join(cols))
for st in ('PRIMARY_BUY', 'T20_ROCKET_WATCH', 'BREAKOUT_READY'):
    sub = x[x['v3_state'].eq(st)]
    s = _tail_stat(sub)
    print(st + ' | ' + ' | '.join(str(s.get(c, '')) for c in cols))
print('base | ' + ' | '.join(str(_tail_stat(x).get(c, '')) for c in cols))

y = x['t0_date'].astype(str).str[:4]
for yr in ('2025', '2026'):
    sub = x[(x['v3_state'].eq('PRIMARY_BUY')) & (y == yr)]
    s = _tail_stat(sub)
    print('PRIMARY_BUY %s: n=%s win=%s mean=%s p90=%s ge20=%s ge30=%s' % (
        yr, s.get('n'), s.get('win'), s.get('mean'), s.get('p90'),
        s.get('ge20'), s.get('ge30')))

sub = x[x['v3_state'].eq('PRIMARY_BUY')]
s = pd.to_numeric(sub['r_break_20'], errors='coerce').dropna()
tot = float(s.sum())
k10 = max(1, int(len(s) * 0.10))
c10 = float(s.nlargest(k10).sum()) / tot * 100 if tot > 0 else 0
print('PRIMARY_BUY Winner贡献 top10=%.1f%% %s' % (c10, 'RIGHT_TAIL' if c10 >= 60 else 'BROAD'))
