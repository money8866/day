# -*- coding: utf-8 -*-
"""Step5 probe 7: stock_basic parquet + master sector fields (throwaway)."""
import json, sys, os
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')
BASE = os.path.dirname(os.path.abspath(__file__))

p = r'D:\mystock\solo\sli\cache\stock_basic.parquet'
print('=== 1. stock_basic parquet ===')
print('exists:', os.path.exists(p))
if os.path.exists(p):
    sb = pd.read_parquet(p)
    print('cols:', sb.columns.tolist())
    print('rows:', len(sb))
    print(sb.head(3).to_string(index=False))
    ind = sb['industry'].dropna()
    print('industry non-null:', len(ind), 'unique:', ind.nunique())
    print('industry samples:', ind.value_counts().head(12).to_dict())
    if 'list_date' in sb.columns:
        print('list_date range:', sb['list_date'].min(), sb['list_date'].max())
    if 'name' in sb.columns:
        print('name ST count:', sb['name'].astype(str).str.contains('ST').sum())

print('\n=== 2. sector_master structure ===')
with open(os.path.join(BASE, 'sector_master.json'), encoding='utf-8') as f:
    m = json.load(f)
print('top keys:', list(m.keys()))
secs = m.get('sectors', [])
print('sectors type:', type(secs).__name__, 'len:', len(secs) if hasattr(secs, '__len__') else '-')
if isinstance(secs, list) and secs:
    allkeys = set()
    for s in secs:
        allkeys.update(s.keys())
    print('union of sector keys:', sorted(allkeys))
    t10 = [s for s in secs if s.get('sector_id') == 'T10']
    if t10:
        print('T10:', json.dumps(t10[0], ensure_ascii=False)[:800])
elif isinstance(secs, dict) and secs:
    k0 = sorted(secs.keys())[0]
    print('first sector key:', k0)
    print('keys:', json.dumps(secs[k0], ensure_ascii=False)[:800])

print('\n=== 3. mapping output exists? ===')
for f_ in ['output/sector_mapping.json', 'data/sector_mapping.csv']:
    fp = os.path.join(BASE, f_)
    print(f_, os.path.exists(fp))

print('\nPROBE7 DONE')
