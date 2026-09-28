# -*- coding: utf-8 -*-
"""One-off input schema check before building P60/P30/P20 features."""
import os, sys, glob
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hef_common import CD, PQ, FS_DATA, DATA, load_calendar

cal = load_calendar()
print('calendar rows %d %s..%s cols=%s' % (
    len(cal), cal['trade_date'].iloc[0], cal['trade_date'].iloc[-1],
    list(cal.columns)))

for f in ('price_panel.parquet', 'basic_panel.parquet'):
    p = os.path.join(FS_DATA, f)
    sch = pq.read_schema(p)
    md = pq.read_metadata(p)
    print('%s rows=%d cols=%s' % (f, md.num_rows, sch.names))

for f in ('price_panel.parquet', 'basic_panel.parquet'):
    d = pd.read_parquet(os.path.join(FS_DATA, f), columns=['ts_code', 'trade_date'])
    print('%s dates %s..%s stocks %d' % (
        f, d['trade_date'].min(), d['trade_date'].max(), d['ts_code'].nunique()))
    del d

print('namechange CD %d PQ %d' % (
    len(glob.glob(os.path.join(CD, 'namechange*'))),
    len(glob.glob(os.path.join(PQ, 'namechange*')))))
fc = glob.glob(os.path.join(CD, 'forecast_??????_??.parquet'))
fp = glob.glob(os.path.join(PQ, 'forecast_vip_*.parquet'))
print('forecast CD %d PQ %d' % (len(fc), len(fp)))
if fc:
    d = pd.read_parquet(fc[0])
    print('forecast sample cols', list(d.columns), 'rows', len(d))
if fp:
    d = pd.read_parquet(fp[0])
    print('forecast_vip sample cols', list(d.columns), 'rows', len(d))

ver = pd.read_parquet(os.path.join(DATA, 'quarterly_versions.parquet'))
print('ver rows %d cols %s' % (len(ver), list(ver.columns)))
ev = pd.read_parquet(os.path.join(DATA, 'events.parquet'))
print('ev rows %d cols %s' % (len(ev), list(ev.columns)))
u = ev.drop_duplicates(subset=['ts_code', 'end_date'])
print('distinct units %d; by ann year %s' % (
    len(u), u['ann'].astype(str).str[:4].value_counts().sort_index().to_dict()))
g = ver.groupby(['ts_code', 'ann'])['end_date'].nunique()
print('share ann occasions with >1 end: %.4f' % float((g > 1).mean()))
g2 = ver.groupby(['ts_code', 'end_date']).size()
print('versions per (ts,end): mean %.3f max %d share>1 %.4f' % (
    g2.mean(), int(g2.max()), float((g2 > 1).mean())))
print('dtypes: ts_code %s end_date %s ann %s' % (
    ver['ts_code'].dtype, ver['end_date'].dtype, ver['ann'].dtype))
