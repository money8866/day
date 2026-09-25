# -*- coding: utf-8 -*-
"""基本面预期差 Alpha 研究 - 覆盖度定量（决定研究分期方案）"""
import os
import re
import glob
import sqlite3
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = []


def p(*a):
    s = ' '.join(str(x) for x in a)
    print(s, flush=True)
    OUT.append(s)


DB = r'D:\mystock\cache_daily\stock_data.db'
c = sqlite3.connect(DB, timeout=180)

p('=' * 70)
p('1) stk_factor_pro 逐日/逐年覆盖（价格+估值长历史可能性）')
p('=' * 70)
q = pd.read_sql_query(
    "SELECT substr(trade_date,1,4) y, COUNT(*) n, COUNT(DISTINCT ts_code) codes, "
    "COUNT(DISTINCT trade_date) days, "
    "SUM(CASE WHEN pe_ttm IS NOT NULL THEN 1 ELSE 0 END) pe_n, "
    "SUM(CASE WHEN total_mv IS NOT NULL THEN 1 ELSE 0 END) mv_n "
    "FROM stk_factor_pro GROUP BY y ORDER BY y", c)
p(q.to_string())

p('')
p('=' * 70)
p('2) daily_basic_cache 逐年覆盖')
p('=' * 70)
q2 = pd.read_sql_query(
    "SELECT substr(trade_date,1,4) y, COUNT(*) n, COUNT(DISTINCT ts_code) codes, "
    "COUNT(DISTINCT trade_date) days FROM daily_basic_cache GROUP BY y ORDER BY y", c)
p(q2.to_string())

p('')
p('=' * 70)
p('3) daily_cache 逐年覆盖')
p('=' * 70)
q3 = pd.read_sql_query(
    "SELECT substr(trade_date,1,4) y, COUNT(*) n, COUNT(DISTINCT ts_code) codes, "
    "COUNT(DISTINCT trade_date) days FROM daily_cache GROUP BY y ORDER BY y", c)
p(q3.to_string())
c.close()

p('')
p('=' * 70)
p('4) treasure_fin_ind_*.parquet 覆盖度（ann_date + end_date 范围）')
p('=' * 70)
g = sorted(glob.glob(r'D:\mystock\cache_daily\treasure_fin_ind_*.parquet'))
p('文件数: %d' % len(g))
rows, mn, mx, codes = 0, '99999999', '00000000', set()
ann_mn, ann_mx = '99999999', '00000000'
sample_cols = None
for fp in g:
    try:
        d = pd.read_parquet(fp, columns=None)
    except Exception:
        continue
    if sample_cols is None:
        sample_cols = list(d.columns)
    rows += len(d)
    if 'ts_code' in d.columns and len(d):
        codes.add(str(d['ts_code'].iloc[0]))
    if 'end_date' in d.columns:
        e = d['end_date'].astype(str)
        mn = min(mn, e.min()); mx = max(mx, e.max())
    if 'ann_date' in d.columns:
        a = d['ann_date'].astype(str)
        ann_mn = min(ann_mn, a.min()); ann_mx = max(ann_mx, a.max())
p('总行数: %d   股票数: %d' % (rows, len(codes)))
p('end_date 范围: %s ~ %s' % (mn, mx))
p('ann_date 范围: %s ~ %s' % (ann_mn, ann_mx))
p('列数: %d' % (len(sample_cols) if sample_cols else 0))

p('')
p('=' * 70)
p('5) 逐股 income/balance/cashflow 覆盖度（含 ann_date）')
p('=' * 70)
for pat in ('income_*.parquet', 'balance_*.parquet', 'cashflow_*.parquet'):
    g = sorted(glob.glob(os.path.join(r'D:\mystock\cache_daily', pat)) +
               glob.glob(os.path.join(r'D:\mystock\cache_daily\parquet', pat)))
    codes = set()
    rows = 0
    mn, mx = '99999999', '00000000'
    for fp in g:
        m = re.search(r'(\d{6}_[A-Z]{2})', os.path.basename(fp))
        if m:
            codes.add(m.group(1))
        try:
            d = pd.read_parquet(fp, columns=['end_date', 'ann_date'])
        except Exception:
            continue
        rows += len(d)
        e = d['end_date'].astype(str)
        mn = min(mn, e.min()); mx = max(mx, e.max())
    p('  %-24s 文件=%-6d 股票=%-5d 行数=%-8d end_date %s~%s'
      % (pat, len(g), len(codes), rows, mn, mx))

p('')
p('=' * 70)
p('6) 行业分类 map')
p('=' * 70)
for fp in (r'D:\mystock\cache_daily\sw_industry_map.csv',
           r'D:\mystock\solo\sli\cache\classify_SW2021_L1.parquet',
           r'D:\mystock\solo\sli\cache\classify_SW2021_L2.parquet'):
    if not os.path.exists(fp):
        p('  %s 不存在' % fp)
        continue
    if fp.endswith('.csv'):
        d = pd.read_csv(fp, dtype=str)
    else:
        d = pd.read_parquet(fp)
    p('  %s shape=%s' % (os.path.basename(fp), d.shape))
    p('    列: %s' % list(d.columns))
    p(d.head(3).to_string())

with open(os.path.join(HERE, '_fs_probe7.txt'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(OUT))
print('DONE')
