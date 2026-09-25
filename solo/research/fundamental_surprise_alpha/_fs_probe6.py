# -*- coding: utf-8 -*-
"""基本面预期差 Alpha 研究 - 价格范围/行业分类/财务覆盖最终确认"""
import os
import re
import glob
import sqlite3
from collections import Counter
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = []


def p(*a):
    s = ' '.join(str(x) for x in a)
    print(s, flush=True)
    OUT.append(s)


DB = r'D:\mystock\cache_daily\stock_data.db'
c = sqlite3.connect(DB, timeout=120)
p('=' * 70)
p('1) 各表 trade_date 范围')
p('=' * 70)
for t, col in (('daily_cache', 'trade_date'), ('index_daily_cache', 'trade_date'),
               ('daily_basic_cache', 'trade_date'), ('stk_factor_pro', 'trade_date'),
               ('adj_factor_cache', 'trade_date'), ('factor_snapshot_cache', 'trade_date')):
    try:
        r = c.execute("SELECT MIN(%s), MAX(%s), COUNT(DISTINCT %s) FROM [%s]" % (col, col, col, t)).fetchone()
        p('  %-24s min=%s max=%s 日期数=%s' % (t, r[0], r[1], r[2]))
    except Exception as e:
        p('  %-24s 失败 %s' % (t, e))
p('')
p('stk_factor_pro 列:')
for row in c.execute("PRAGMA table_info(stk_factor_pro)"):
    p('   %s %s' % (row[1], row[2]))
c.close()

p('')
p('=' * 70)
p('2) 行业分类来源搜索')
p('=' * 70)
pats = ('**/*sw*industry*', '**/*industry*.csv', '**/*industry*.json', '**/*classify*',
        '**/theme_stock_map*', '**/*stock_industry*', '**/sw_*.json', '**/*_sw*')
for root in (r'D:\mystock\cache_daily', r'D:\mystock\solo'):
    for pat in pats:
        for fp in glob.glob(os.path.join(root, pat), recursive=True):
            if '__pycache__' in fp or not os.path.isfile(fp):
                continue
            p('  %9.1f KB  %s' % (os.path.getsize(fp) / 1e3, fp))
p('（对照：theme 相关 map 文件）')
for fp in glob.glob(r'D:\mystock\solo\report_daily\theme_stock_map*.json')[:3]:
    p('  %s' % fp)

p('')
p('=' * 70)
p('3) fina_indicator 家族与逐股财报文件覆盖')
p('=' * 70)
SUB = r'D:\mystock\cache_daily\parquet'
TOP = r'D:\mystock\cache_daily'
for label, folder in (('parquet/', SUB), ('顶层', TOP)):
    for pat in ('fina_indicator_*.parquet', 'fina_indicator_q_*.parquet',
                'fina_indicator_yoy_*.parquet', 'income_*.parquet',
                'balance_*.parquet', 'cashflow_*.parquet', 'forecast_*.parquet'):
        g = glob.glob(os.path.join(folder, pat))
        codes = set()
        for fp in g:
            m = re.search(r'(\d{6}\.[A-Z]{2})', os.path.basename(fp))
            if m:
                codes.add(m.group(1))
        if g:
            p('  %-8s %-32s 文件=%-6d 股票=%-5d' % (label, pat, len(g), len(codes)))

p('')
p('=' * 70)
p('4) 扣非/营收/现金流 是否含 ann_date 及历史长度（抽 5 只）')
p('=' * 70)
for code in ('000001.SZ', '600519.SH', '300750.SZ', '688981.SH', '002594.SZ'):
    tag = code.replace('.', '_')
    p('--- %s' % code)
    for pat in ('fina_indicator_%s_*.parquet' % code,
                'fina_indicator_q_%s_*.parquet' % code,
                'income_%s_*.parquet' % tag,
                'balance_%s*.parquet' % tag,
                'cashflow_%s_*.parquet' % tag):
        g = glob.glob(os.path.join(SUB, pat)) + glob.glob(os.path.join(TOP, pat))
        if not g:
            p('    %-42s 无' % pat)
            continue
        fp = g[0]
        try:
            d = pd.read_parquet(fp)
            has_ann = 'ann_date' in d.columns
            rng = ''
            if 'end_date' in d.columns:
                ed = d['end_date'].astype(str)
                rng = '%s~%s(%d期)' % (ed.min(), ed.max(), ed.nunique())
            p('    %-42s rows=%-5d ann=%s %s' % (os.path.basename(fp)[:42], len(d), has_ann, rng))
        except Exception as e:
            p('    %-42s 读取失败 %s' % (os.path.basename(fp)[:42], e))

p('')
p('=' * 70)
p('5) treasure_fin_ind / treasure_zbin 样例')
p('=' * 70)
for pat in ('treasure_fin_ind*.parquet', 'treasure_zbin*.parquet', 'treasure_namechg*.parquet'):
    g = sorted(glob.glob(os.path.join(TOP, pat)))[:1]
    for fp in g:
        try:
            d = pd.read_parquet(fp)
            p('  %s shape=%s' % (os.path.basename(fp), d.shape))
            p('   列: %s' % list(d.columns))
            p('   head:\n%s' % d.head(3).to_string())
        except Exception as e:
            p('  %s 读取失败 %s' % (fp, e))

with open(os.path.join(HERE, '_fs_probe6.txt'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(OUT))
print('DONE')
