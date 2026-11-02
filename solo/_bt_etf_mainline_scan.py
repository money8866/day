# -*- coding: utf-8 -*-
"""全年扫描 ETF 主线识别效果"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
import numpy as np
import etf_mainline as em

cfg = em.load_config()
panel = em.load_etf_panel()
dates = sorted(panel['trade_date'].unique())
d26 = [d for d in dates if d >= '20260101']
print(f'扫描 2026 全年 {len(d26)} 个交易日\n')

rows = []
for d in d26:
    r = em.detect_mainlines(d, cfg, panel)
    inds = r['industries']
    det = r['details']
    row = {'d': d, 'n_mainline': len(inds), 'mainlines': '|'.join(inds)}
    # 取相对强度第一的行业
    if det:
        best = max(det.keys(), key=lambda i: (det[i]['excess20'] or -999))
        row['top_ind'] = best
        row['top_ex20'] = det[best]['excess20']
        row['top_ex60'] = det[best]['excess60']
        row['top_ret20'] = det[best]['ret20']
        row['top_passed'] = int(det[best]['passed'])
        row['top_days'] = det[best].get('days_strong') or 0
        row['top_amt'] = det[best]['amt_ratio']
        row['top_breadth'] = det[best]['up_ratio']
    rows.append(row)
    if (len(rows)) % 25 == 0:
        print(f'  已扫描 {len(rows)}/{len(d26)}', flush=True)

df = pd.DataFrame(rows)
df.to_csv('_bt_out/etf_mainline_scan.csv', index=False)

print()
print('=' * 74)
print('主线识别结果汇总')
print('=' * 74)
print('有主线的天数:', (df.n_mainline > 0).sum(), '/', len(df))
print('主线行业分布:')
from collections import Counter
c = Counter()
for s in df.mainlines:
    for x in (s.split('|') if s else []):
        c[x] += 1
for k, v in c.most_common():
    print(f'   {k:<12} {v:>3} 天')
print()
print('按月统计:')
df['ym'] = df.d.str[:6]
m = df.groupby('ym').agg(有主线=('n_mainline', lambda s: (s > 0).sum()), 天数=('n_mainline', 'size'),
                          top_ex20=('top_ex20', 'mean'), top_pass=('top_passed', 'mean'))
print(m.round(1).to_string())
