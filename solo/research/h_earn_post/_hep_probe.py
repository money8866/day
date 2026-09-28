# -*- coding: utf-8 -*-
"""H-EARN-POST 数据侦察：确认 Tushare cache 可用字段与中报覆盖"""
import os
import glob
import collections
import numpy as np
import pandas as pd

CD = r'D:\mystock\cache_daily'
PD = os.path.join(CD, 'parquet')


def fam(prefixes, dirs=(CD, PD)):
    out = collections.Counter()
    for d in dirs:
        for f in os.listdir(d):
            if f.endswith('.parquet'):
                out[f.split('_')[0]] += 1
    return out


print('=' * 70)
print('1) parquet 文件族计数（只看前 30）')
c = fam(None)
for k, v in sorted(c.items(), key=lambda x: -x[1])[:30]:
    print('   %-24s %d' % (k, v))

print('=' * 70)
print('2) 目录清单')
for d in (CD, PD):
    subs = [x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x))]
    print('   %s 子目录=%s' % (d, subs[:20]))
    csvs = glob.glob(os.path.join(d, '*.csv'))
    print('   %s CSV=%s' % (d, [os.path.basename(x) for x in csvs][:30]))

print('=' * 70)
print('3) 目录/行业/指数/日历文件')
for pat in ('industry*', 'index*', 'trade_cal*', 'stock_basic*', 'daily_basic*',
            'treasure_namechg*', 'income*', 'cashflow*'):
    for d in (CD, PD, os.path.join(CD, 'industry')):
        g = glob.glob(os.path.join(d, pat))
        if g:
            print('   %-28s -> %3d 个  例: %s' % (
                os.path.join(os.path.basename(d), pat), len(g),
                os.path.basename(g[0])))
            break

print('=' * 70)
print('4) 日历')
cal = pd.read_parquet(r'd:\mystock\solo\research\fundamental_surprise_alpha\data\calendar.parquet')
td = cal['trade_date'].astype(str).values
print('   交易日 %d  %s ~ %s' % (len(td), td[0], td[-1]))
print('   2026 年交易日数 =', int((td >= '20260101').sum()))

print('=' * 70)
print('5) 2026 中报（end_date=20260630）覆盖探测')
inc_files = []
for d in (CD, PD):
    inc_files += glob.glob(os.path.join(d, 'income_*.parquet'))
print('   income 文件数 =', len(inc_files))
rows0630 = 0
rows_all = 0
yrs = collections.Counter()
sample_cols = None
for fp in inc_files[:400]:
    try:
        d = pd.read_parquet(fp)
    except Exception:
        continue
    if sample_cols is None:
        sample_cols = list(d.columns)
    if 'end_date' not in d.columns:
        continue
    e = d['end_date'].astype(str).str.replace('.0', '', regex=False)
    rows_all += len(d)
    m = e == '20260630'
    rows0630 += int(m.sum())
    for y in e[e.str.endswith('0630')].str[:4]:
        yrs[y] += 1
print('   前 400 个 income 文件: 行=%d  20260630 行=%d' % (rows_all, rows0630))
print('   中报(0630) 年份分布 =', dict(sorted(yrs.items())))
print('   income 列 =', sample_cols)

print('=' * 70)
print('6) 指数（沪深300）')
for cand in ('index_daily', 'index_dailybasic'):
    g = glob.glob(os.path.join(PD, cand + '*')) + glob.glob(os.path.join(CD, cand + '*'))
    print('   %s -> %d 例: %s' % (cand, len(g), [os.path.basename(x) for x in g[:5]]))
ip = r'd:\mystock\solo\research\fundamental_surprise_alpha\data\index_panel.parquet'
if os.path.exists(ip):
    d = pd.read_parquet(ip)
    print('   index_panel: shape=%s cols=%s' % (d.shape, list(d.columns)))
    print('   date range %s ~ %s' % (d['trade_date'].min(), d['trade_date'].max()))
print('=' * 70)
print('DONE')
