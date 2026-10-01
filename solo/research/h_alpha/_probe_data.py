# -*- coding: utf-8 -*-
"""Throw-away probe 4: panel + basic + industry availability. Read-only."""
import os
import sqlite3
import pandas as pd

FS = r'd:\mystock\solo\research\fundamental_surprise_alpha\data'
print('--- research panel dir')
for f in sorted(os.listdir(FS)):
    p = os.path.join(FS, f)
    print('   %-34s %10.2f MB' % (f, os.path.getsize(p) / 1e6))

for f in ('price_panel.parquet', 'basic_panel.parquet', 'calendar.parquet'):
    p = os.path.join(FS, f)
    if not os.path.exists(p):
        continue
    d = pd.read_parquet(p)
    print('\n=== %s  shape %s' % (f, d.shape))
    print('    cols', list(d.columns)[:30])
    if 'trade_date' in d.columns:
        print('    trade_date min %s max %s uniq %d'
              % (d['trade_date'].min(), d['trade_date'].max(),
                 d['trade_date'].nunique()))
    print(d.head(3).to_string()[:900])

print('\n--- stock_data.db')
c = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')
tabs = [r[0] for r in c.execute(
    "select name from sqlite_master where type='table'")]
for n in tabs:
    cols = [r[1] for r in c.execute('pragma table_info("%s")' % n)]
    try:
        cnt = c.execute('select count(*) from "%s"' % n).fetchone()[0]
    except Exception:
        cnt = -1
    print('   %-24s %10d  %s' % (n, cnt, cols[:30]))

print('\n--- other cache_daily files')
CD = r'D:\mystock\cache_daily'
for f in sorted(os.listdir(CD)):
    p = os.path.join(CD, f)
    if os.path.isfile(p):
        print('   %-44s %10.2f MB' % (f, os.path.getsize(p) / 1e6))
