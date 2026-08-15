# -*- coding: utf-8 -*-
import sys, os, time
sys.path.insert(0, r'd:\mystock\solo')
sys.stdout.reconfigure(encoding='utf-8')
from bts.scanner import _backtest_one
from bts.data import get_stock_pool

pool = get_stock_pool().head(40)
t0 = time.time()
all_rows = []
for _, r in pool.iterrows():
    rows = _backtest_one((r['ts_code'], r.get('name', ''), '20240101', '20260814', 20))
    if rows:
        all_rows.extend(rows)
print(f'40只 步长20 耗时 {time.time()-t0:.1f}s, 信号 {len(all_rows)}')
if all_rows:
    s = all_rows[0]
    print('样例字段:', {k: s[k] for k in ('ts_code', 'name', 'date', 'bts', 'grade', 'signal', 'breakout_date', 'days_after', 'fut5', 'fut10', 'fut20', 'fut_max_dd')})
    import collections
    print('等级分布:', collections.Counter(x['grade'] for x in all_rows))
    import numpy as np
    f5 = [x['fut5'] for x in all_rows if x['fut5'] == x['fut5']]
    print(f'fut5均值 {np.mean(f5):.2f}% 胜率 {np.mean([1 if x>0 else 0 for x in f5])*100:.1f}%')
