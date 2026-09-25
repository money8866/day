# -*- coding: utf-8 -*-
import pandas as pd
p = r'd:\mystock\solo\report_daily\hvt_bull_backtest_events_20250101_20260828.csv'
d = pd.read_csv(p)
print('shape', d.shape)
print('cols', list(d.columns))
print(d.head(3).to_string()[:2000])
