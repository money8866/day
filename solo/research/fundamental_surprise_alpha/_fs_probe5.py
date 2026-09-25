# -*- coding: utf-8 -*-
"""基本面预期差 Alpha 研究 - cache_daily/parquet 子目录普查（财报逐股缓存）"""
import os
import re
import glob
from collections import Counter, defaultdict
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = []


def p(*a):
    s = ' '.join(str(x) for x in a)
    print(s, flush=True)
    OUT.append(s)


SUB = r'D:\mystock\cache_daily\parquet'
p('=' * 70)
p('cache_daily\\parquet 子目录普查')
p('=' * 70)
files = [f for f in os.listdir(SUB) if os.path.isfile(os.path.join(SUB, f))]
p('文件总数: %d' % len(files))

# 归一化：前缀 + 是否含 ts_code + 是否含期次
pat_ts = re.compile(r'^\d{6}\.[A-Z]{2}$')
fam = Counter()
period_map = defaultdict(set)
code_map = defaultdict(set)
for f in files:
    stem = os.path.splitext(f)[0]
    parts = stem.split('_')
    pref = parts[0]
    kind = 'other'
    codes = set()
    periods = set()
    for tk in parts[1:]:
        if pat_ts.match(tk):
            codes.add(tk)
        elif re.fullmatch(r'\d{8}', tk):
            periods.add(tk)
            kind = 'period8'
        elif re.fullmatch(r'20\d{2}', tk):
            periods.add(tk)
            kind = 'year4'
        elif re.fullmatch(r'20\d{2}[QH]?[1-4]?', tk):
            periods.add(tk)
    sig = '%s|%s|ncode=%d' % (pref, kind, len(codes))
    fam[sig] += 1
    if periods:
        period_map[sig] |= periods
    for c in codes:
        code_map[sig].add(c)

p('')
p('按「前缀|期次格式」分组:')
for k, v in fam.most_common(30):
    cds = len(code_map[k])
    prd = sorted(period_map[k])
    prs = ('%s..%s(%d)' % (prd[0], prd[-1], len(prd))) if prd else '-'
    p('   %-34s 文件=%-6d 股票=%-5d 期次=%s' % (k, v, cds, prs))

p('')
p('=' * 70)
p('fina_indicator_*.parquet 结构样例')
p('=' * 70)
g = sorted(glob.glob(os.path.join(SUB, 'fina_indicator_*.parquet')))
p('命中 %d 个' % len(g))
for fp in ([g[0], g[len(g) // 2], g[-1]] if g else []):
    d = pd.read_parquet(fp)
    p('  %s  shape=%s' % (os.path.basename(fp), d.shape))
    p('    列: %s' % list(d.columns))
    if 'end_date' in d.columns:
        p('    end_date: %s' % sorted(d['end_date'].astype(str).unique())[:30])
    if 'ann_date' in d.columns:
        p('    ann_date: %s' % sorted(d['ann_date'].astype(str).unique())[:30])

p('')
p('=' * 70)
p('cashflow / income / balance / express 样例')
p('=' * 70)
for pat in ('cashflow_*.parquet', 'income_*.parquet', 'balance_*.parquet',
            'express*.parquet', 'balancesheet*.parquet', 'forecast*.parquet'):
    g = sorted(glob.glob(os.path.join(SUB, pat)))
    p('--- %s 命中 %d' % (pat, len(g)))
    for fp in g[:1]:
        try:
            d = pd.read_parquet(fp)
            p('   %s shape=%s' % (os.path.basename(fp), d.shape))
            p('   列: %s' % list(d.columns))
            p('   head:\n%s' % d.head(4).to_string())
        except Exception as e:
            p('   读取失败', e)

with open(os.path.join(HERE, '_fs_probe5.txt'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(OUT))
print('DONE')
