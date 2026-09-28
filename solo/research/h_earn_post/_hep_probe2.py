# -*- coding: utf-8 -*-
"""H-EARN-POST 侦察 2：SQLite cache 覆盖范围 + 行业/指数/名称变更"""
import os
import glob
import sqlite3
import pandas as pd

DB = r'D:\mystock\cache_daily\stock_data.db'
CD = r'D:\mystock\cache_daily'
PD = os.path.join(CD, 'parquet')

c = sqlite3.connect(DB, timeout=300)
print('=' * 70)
print('1) 表清单')
tabs = [r[0] for r in c.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
print('   ', tabs)

print('=' * 70)
print('2) 主要表覆盖')
for t in tabs:
    try:
        n = c.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
        cols = [r[1] for r in c.execute('PRAGMA table_info("%s")' % t)]
        dcol = 'trade_date' if 'trade_date' in cols else None
        if dcol and n:
            mn, mx = c.execute(
                'SELECT MIN(%s),MAX(%s) FROM "%s"' % (dcol, dcol, t)).fetchone()
            print('   %-26s rows=%-10d %s ~ %s' % (t, n, mn, mx))
        else:
            print('   %-26s rows=%-10d cols=%s' % (t, n, cols[:12]))
    except Exception as ex:
        print('   %-26s ERR %s' % (t, str(ex)[:60]))
c.close()

print('=' * 70)
print('3) 行业映射')
for fp in glob.glob(os.path.join(CD, 'industry', '*')):
    print('   ', os.path.basename(fp), os.path.getsize(fp))
p = os.path.join(CD, 'industry', 'sw_industry_map.csv')
if os.path.exists(p):
    d = pd.read_csv(p, dtype=str)
    print('   sw_industry_map cols=%s rows=%d' % (list(d.columns), len(d)))
    print(d.head(3).to_string())

print('=' * 70)
print('4) 指数文件（000300 / 000852 / 932000）')
for pat in ('index_daily_000300*', 'index_daily_000852*', 'index_daily_932000*',
            'index_daily_000905*'):
    g = sorted(glob.glob(os.path.join(PD, pat)))
    print('   %-26s %d 个' % (pat, len(g)))
    if g:
        d = pd.read_parquet(g[-1])
        print('      例 %s rows=%d %s ~ %s' % (
            os.path.basename(g[-1]), len(d), d['trade_date'].min(), d['trade_date'].max()))

print('=' * 70)
print('5) income / balance / cashflow / fin_ind 文件命名 + 2026中报全量计数')
import collections
cnt = collections.Counter()
tab_cols = {}
for pre in ('income', 'balance', 'cashflow', 'treasure_fin_ind'):
    files = []
    for d in (CD, PD):
        files += glob.glob(os.path.join(d, pre + '_*.parquet'))
    print('   %-18s 文件=%d' % (pre, len(files)))
    y = collections.Counter()
    colsm = None
    for fp in files[:600]:
        try:
            x = pd.read_parquet(fp)
        except Exception:
            continue
        if colsm is None:
            colsm = list(x.columns)
        if 'end_date' in x.columns:
            e = x['end_date'].astype(str).str.replace('.0', '', regex=False)
            for yy in e[e.str.endswith('0630')].str[:4]:
                y[yy] += 1
    tab_cols[pre] = colsm
    print('       列 =', colsm)
    print('       0630 年份(前600文件) =', dict(sorted(y.items())))

print('=' * 70)
print('6) stock_basic')
sb = pd.read_csv(os.path.join(CD, 'stock_basic.csv'), dtype=str)
print('   rows=%d cols=%s' % (len(sb), list(sb.columns)))
print('=' * 70)
print('DONE')
