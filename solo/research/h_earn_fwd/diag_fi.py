# -*- coding: utf-8 -*-
"""one-shot diagnostic: why fina fields are all-NaN in quarterly_versions."""
import os, sys, glob, collections
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hef_common import CD, PQ, DATA

print('A. fina files in PQ: %d' % len(
    len(glob.glob(os.path.join(PQ, 'fina_indicator*.parquet'))) * [1]))
files = sorted(glob.glob(os.path.join(PQ, 'fina_indicator*.parquet')))
names = [os.path.basename(p) for p in files]
print('   first:', names[:3])
print('   last:', names[-3:])
files_cd = sorted(glob.glob(os.path.join(CD, 'fina_indicator*.parquet')))
print('A2. fina files in CD root: %d e.g. %s'
      % (len(files_cd), [os.path.basename(x) for x in files_cd[:3]]))

schemas = collections.Counter()
sample_by_schema = {}
for p in files[::max(1, len(files) // 60)][:60]:
    try:
        d = pd.read_parquet(p)
    except Exception:
        continue
    key = tuple(d.columns)
    schemas[key] += 1
    if key not in sample_by_schema:
        sample_by_schema[key] = p
print('B. schema survey on %d sampled files:' % min(60, len(files)))
for cols, n in schemas.items():
    p = sample_by_schema[cols]
    d = pd.read_parquet(p)
    nn = ('%.3f' % d['profit_dedt'].notna().mean()
          if 'profit_dedt' in d.columns else 'NO COL')
    print('   n=%d nn(profit_dedt)=%s e.g. %s'
          % (n, nn, os.path.basename(p)))
    if 'profit_dedt' in d.columns:
        print('      sample end:', d['end_date'].head(2).tolist(),
              'ts:', d['ts_code'].head(2).tolist(),
              'profit_dedt:', d['profit_dedt'].dropna().head(2).tolist())

want = ['ts_code', 'end_date', 'ann_date', 'f_ann_date',
        'profit_dedt', 'gross_margin', 'roe', 'debt_to_assets', 'roic']
dfs, n_have = [], 0
for p in files:
    try:
        d = pd.read_parquet(p)
    except Exception:
        continue
    if 'ts_code' not in d.columns:
        continue
    cols = [c for c in want if c in d.columns]
    if 'profit_dedt' in cols:
        n_have += 1
    dfs.append(d[cols].drop_duplicates())
df = pd.concat(dfs, ignore_index=True)
print('C. concat rows %d, cols %s' % (len(df), list(df.columns)))
print('   files carrying profit_dedt: %d' % n_have)
print(df.notna().mean().to_string())

from hef_build_events import load_family_lean
BUILD_WANT = ['ts_code', 'end_date', 'profit_dedt',
              'gross_margin', 'roe', 'debt_to_assets', 'roic']
fi = load_family_lean([(PQ, 'fina_indicator_*.parquet')], BUILD_WANT)
fi = fi.drop_duplicates(subset=['ts_code', 'end_date'], keep='last')
fi = fi[fi['end_date'] >= '20150331']
print('D. build-path fi rows %d stocks %d' % (len(fi), fi['ts_code'].nunique()))
print(fi.notna().mean().to_string())
print('   end sample:', fi['end_date'].head(3).tolist(),
      'ts sample:', fi['ts_code'].head(3).tolist())

ver = pd.read_parquet(os.path.join(DATA, 'quarterly_versions.parquet'),
                      columns=['ts_code', 'end_date'])
print('E. ver rows %d ts dtype %s end dtype %s'
      % (len(ver), ver['ts_code'].dtype, ver['end_date'].dtype))
print('   ver end sample:', ver['end_date'].dropna().head(3).tolist())
verk = ver.drop_duplicates().copy()
verk['ts_code'] = verk['ts_code'].astype('object')
verk['end_date'] = verk['end_date'].astype('object')
fk = fi[['ts_code', 'end_date']].drop_duplicates()
inter = verk.merge(fk, on=['ts_code', 'end_date'])
print('F. key overlap: %d matched of %d ver-uniques, %d fi-uniques'
      % (len(inter), len(verk), len(fk)))
print('DIAG DONE')
