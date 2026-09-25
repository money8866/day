# -*- coding: utf-8 -*-
"""基本面预期差 Alpha 研究 - 财务表结构与覆盖度侦察（只读）"""
import os
import sqlite3
import numpy as np
import pandas as pd

DB = r'D:\mystock\cache_daily\stock_data.db'
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = []


def p(*a):
    s = ' '.join(str(x) for x in a)
    print(s, flush=True)
    OUT.append(s)


c = sqlite3.connect(DB, timeout=120)


def dump_table(name, head=0, sample_cols=None):
    p('')
    p('-' * 68)
    p('表: %s' % name)
    p('-' * 68)
    sch = c.execute("PRAGMA table_info([%s])" % name).fetchall()
    p('列数 %d :' % len(sch))
    for col in sch:
        p('   %-26s %s' % (col[1], col[2]))
    if head:
        cols = sample_cols or [x[1] for x in sch]
        d = pd.read_sql_query("SELECT %s FROM [%s] LIMIT %d"
                              % (','.join('[%s]' % x for x in cols), name, head), c)
        p(d.to_string())


for t in ('fina_indicator_cache', 'daily_basic_cache', 'cache_meta',
          'factor_snapshot_cache', 'adj_factor_cache'):
    dump_table(t, head=3)

p('')
p('=' * 70)
p('fina_indicator_cache 覆盖度')
p('=' * 70)
d = pd.read_sql_query("SELECT * FROM fina_indicator_cache", c)
p('shape:', d.shape)
for col in d.columns:
    if col.lower() in ('end_date', 'ann_date', 'trade_date', 'ts_code'):
        continue
    nn = d[col].notna().mean()
    p('   %-24s  非空率=%.3f  样例=%s' % (col, nn, list(d[col].dropna().head(3).values)))

for col in ('end_date', 'ann_date'):
    if col in d.columns:
        v = d[col].astype(str)
        p('%s: min=%s max=%s 唯一值数=%d' % (col, v.min(), v.max(), v.nunique()))
        p('  期末分布(前20):')
        p('   ' + str(v.value_counts().sort_index().to_dict())[:1500])
p('股票数:', d['ts_code'].nunique() if 'ts_code' in d.columns else 'N/A')

p('')
p('=' * 70)
p('daily_basic_cache 覆盖度')
p('=' * 70)
db = pd.read_sql_query("SELECT * FROM daily_basic_cache", c)
p('shape:', db.shape)
p('列:', list(db.columns))
td = db['trade_date'].astype(str)
p('trade_date min=%s max=%s 交易日数=%d' % (td.min(), td.max(), td.nunique()))
p('股票数:', db['ts_code'].nunique())
if 'ann_date' not in db.columns:
    p('（无 ann_date 列）')

p('')
p('=' * 70)
p('cache_meta 内容')
p('=' * 70)
try:
    m = pd.read_sql_query("SELECT * FROM cache_meta", c)
    p(m.to_string())
except Exception as e:
    p('读取失败', e)

p('')
p('=' * 70)
p('index_daily_cache 可用指数')
p('=' * 70)
ix = pd.read_sql_query(
    "SELECT ts_code, MIN(trade_date) mn, MAX(trade_date) mx, COUNT(*) n "
    "FROM index_daily_cache GROUP BY ts_code ORDER BY n DESC", c)
p(ix.to_string())

c.close()

p('')
p('=' * 70)
p('stock_basic.csv')
p('=' * 70)
sb_path = r'D:\mystock\cache_daily\stock_basic.csv'
if os.path.exists(sb_path):
    sb = pd.read_csv(sb_path, dtype=str)
    p('shape:', sb.shape)
    p('列:', list(sb.columns))
    p(sb.head(3).to_string())
else:
    p('不存在:', sb_path)

with open(os.path.join(HERE, '_fs_probe2.txt'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(OUT))
print('DONE')
