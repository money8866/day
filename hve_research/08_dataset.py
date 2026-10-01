# -*- coding: utf-8 -*-
"""HVE-Research V1  Step 8  交付物 01_hve_event_dataset.csv"""
import os
import numpy as np
import pandas as pd
import hve_lib as L

OUT = r'D:\mystock\hve_research\out'
DATA = L.DATA

FEAT = ['ts_code', 'name', 'industry', 'board', 'trade_date', 'td_idx', 'row',
        'open', 'high', 'low', 'close', 'pct_chg', 'turnover_rate', 'total_mv',
        'vr5', 'vr10', 'vr20', 'vr60', 'amt_r20', 'vol_pct250', 'amt_pct250',
        'turn_pct250', 'clv', 'ma20', 'ma60', 'ma20_slope5', 'ma20_slope10',
        'dist_ma20', 'dist_ma60', 'dist_hh20', 'dist_hh60', 'ret5', 'ret20',
        'ret60', 'vol20', 'up_limit', 'yizi', 'obs_days']
OC = [f'ret_{h}' for h in (1, 3, 5, 10, 20, 40, 60)] + \
     [f'mfe_{h}' for h in (5, 10, 20)] + [f'mae_{h}' for h in (5, 10, 20)] + \
     [f'volr_{h}' for h in (5, 10, 20)] + \
     [f'exc_{h}' for h in (5, 10, 20)] + [f'excmed_{h}' for h in (5, 10, 20)]
W7 = ['minvr10', 'volr10', 'mae10', 'mfe10', 'struct_min', 'trend', 'struct',
      'contr', 'd_br20', 'd_br60', 'd_brhve', 'd_brloc', 'd_reexp', 'd_pull']


def main():
    import importlib
    pool = pd.read_parquet(os.path.join(DATA, 'pool.parquet'))
    ev = importlib.import_module('02_hve_events').cluster_events(
        pool, 'vr20', 2.0, 5, 'first').reset_index(drop=True)
    w7 = pd.read_parquet(os.path.join(DATA, 'events_w7.parquet'))
    keep = [c for c in W7 if c in w7.columns]
    ev = ev.merge(w7[['row'] + keep], on='row', how='left')
    cols = [c for c in FEAT if c in ev.columns] + \
           [c for c in OC if c in ev.columns] + keep
    d = ev[cols].copy()
    d['segment'] = np.where(d['trade_date'] <= 20230630, 'IS', 'OOS')
    d['hve_def'] = 'vr20>=2.0 & cluster(gap=5,first)'
    for c in d.columns:
        if d[c].dtype.kind == 'f':
            d[c] = d[c].astype(np.float64).round(6)
    d.to_csv(os.path.join(OUT, '01_hve_event_dataset.csv'), index=False,
             encoding='utf-8-sig')
    print('rows', len(d), 'cols', len(d.columns))
    print(d.head(3).to_string())


if __name__ == '__main__':
    main()
