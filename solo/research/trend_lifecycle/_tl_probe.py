# -*- coding: utf-8 -*-
"""TL-01 数据可得性探针（只读；不写任何产出）"""
import os
import glob
import pandas as pd

FS = r'd:\mystock\solo\research\fundamental_surprise_alpha\data'
CD = r'D:\mystock\cache_daily'
PD = os.path.join(CD, 'parquet')


def head(fp, n=3):
    print('=' * 70)
    print(fp)
    try:
        d = pd.read_parquet(fp)
    except Exception as e:
        print('  ERR', e)
        return None
    print('  shape=%s' % (d.shape,))
    print('  cols=%s' % list(d.columns))
    print(d.head(n).to_string())
    for c in ('trade_date', 'end_date'):
        if c in d.columns:
            s = d[c].astype(str)
            print('  %s range %s ~ %s  n_uniq=%d' % (c, s.min(), s.max(), s.nunique()))
    if 'ts_code' in d.columns:
        print('  ts_code n=%d' % d['ts_code'].nunique())
    return d


for f in ('calendar.parquet', 'price_panel.parquet', 'index_panel.parquet',
          'basic_panel.parquet'):
    head(os.path.join(FS, f))

print('#' * 70)
print('cache_daily 顶层 parquet 清单')
files = []
for d in (CD, PD):
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.endswith('.parquet'):
                files.append(os.path.join(d, f))
print('  parquet 文件数 = %d' % len(files))
pref = {}
for fp in files:
    b = os.path.basename(fp)
    key = b.split('_')[0]
    pref.setdefault(key, []).append(b)
for k in sorted(pref):
    print('  %-24s %d  例: %s' % (k, len(pref[k]), pref[k][0]))

print('#' * 70)
print('cache_daily 子目录')
for d in sorted(os.listdir(CD)):
    p = os.path.join(CD, d)
    if os.path.isdir(p):
        try:
            sub = os.listdir(p)
        except Exception:
            continue
        print('  %-28s %d 项  例: %s' % (d, len(sub), sub[:6]))
    elif d.endswith(('.csv', '.db')):
        print('  %-28s (file)  %.1f MB' % (d, os.path.getsize(p) / 1e6))
