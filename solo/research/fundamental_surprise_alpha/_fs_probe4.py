# -*- coding: utf-8 -*-
"""基本面预期差 Alpha 研究 - cache_daily 文件类型普查 + 逐股财报文件结构"""
import os
import re
import json
import glob
from collections import Counter
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = []


def p(*a):
    s = ' '.join(str(x) for x in a)
    print(s, flush=True)
    OUT.append(s)


CD = r'D:\mystock\cache_daily'
p('=' * 70)
p('cache_daily 顶层文件类型普查')
p('=' * 70)
cnt = Counter()
ext = Counter()
for f in os.listdir(CD):
    fp = os.path.join(CD, f)
    if not os.path.isfile(fp):
        continue
    ext[os.path.splitext(f)[1].lower()] += 1
    m = re.match(r'^([A-Za-z_]+?)(?:_\d{6}_[A-Z]{2})?\.', f)
    key = m.group(1) if m else f.split('.')[0][:20]
    cnt[key] += 1
p('顶层文件数: %d' % sum(ext.values()))
p('按扩展名: %s' % dict(ext.most_common()))
p('按前缀(前40):')
for k, v in cnt.most_common(40):
    p('   %-26s %d' % (k, v))

p('')
p('=' * 70)
p('含财报语义的逐股文件样例')
p('=' * 70)
for pat in ('forecast_*.parquet', 'express_*.parquet', 'fina_*.parquet',
            'income_*.parquet', 'balance*.parquet', 'cashflow*.parquet',
            'disclosure*.parquet', '*_fina*', 'audit_*.json'):
    g = glob.glob(os.path.join(CD, '**', pat), recursive=True)
    p('  %-26s 命中 %d 个  例: %s' % (pat, len(g), g[0] if g else '-'))

p('')
p('=' * 70)
p('forecast_000049_SZ.parquet 结构')
p('=' * 70)
fp = os.path.join(CD, 'forecast_000049_SZ.parquet')
if os.path.exists(fp):
    d = pd.read_parquet(fp)
    p('shape:', d.shape)
    p('列:', list(d.columns))
    p(d.head(10).to_string())
else:
    p('不存在')

p('')
p('audit_000049_SZ.json 结构')
p('=' * 70)
fp = os.path.join(CD, 'audit_000049_SZ.json')
if os.path.exists(fp):
    with open(fp, 'r', encoding='utf-8') as f:
        o = json.load(f)
    p('类型:', type(o).__name__)
    p(str(o)[:1200])
else:
    p('不存在')

with open(os.path.join(HERE, '_fs_probe4.txt'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(OUT))
print('DONE')
