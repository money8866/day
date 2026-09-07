# -*- coding: utf-8 -*-
import os, sys, time
sys.path.insert(0, r'd:\mystock\solo')
from _hvt_run_loader import HvtDataLoaderOld

t0 = time.time()
ld = HvtDataLoaderOld()
print('init', round(time.time() - t0, 3))
for code in ('000001.SZ', '600519.SH', '300750.SZ'):
    t1 = time.time()
    df = ld.load(code, '20240801', '20260904')
    print(code, 'rows=', 0 if df is None else len(df), 'sec=', round(time.time() - t1, 3), flush=True)
t2 = time.time()
cs = ld.query_cross_section('20260904', fields=('ts_code', 'turnover_rate', 'amount', 'total_mv'))
print('cross_section rows=', 0 if cs is None else len(cs), 'sec=', round(time.time() - t2, 3), flush=True)
print('TOTAL', round(time.time() - t0, 3))
