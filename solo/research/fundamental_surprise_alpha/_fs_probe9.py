# -*- coding: utf-8 -*-
"""probe9: 逐股日线 parquet 覆盖范围（决定样本期边界）"""
import os, re, glob, random
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CD = r'D:\mystock\cache_daily'
OUT = []


def p(*a):
    s = ' '.join(str(x) for x in a)
    print(s, flush=True)
    OUT.append(s)


PD = os.path.join(CD, 'parquet')
files = [f for f in os.listdir(PD) if f.startswith('daily_') and not f.startswith('daily_basic')]
p('parquet/ 下 daily_* 文件数: %d' % len(files))

# 文件名模式统计
pat = re.compile(r'^daily_(\d{6}\.[A-Z]{2})_(\d{8})_(\d{8})\.parquet$')
starts, ends, ncode = {}, {}, set()
bad = 0
for f in files:
    m = pat.match(f)
    if not m:
        bad += 1
        continue
    ncode.add(m.group(1))
    starts[m.group(2)] = starts.get(m.group(2), 0) + 1
    ends[m.group(3)] = ends.get(m.group(3), 0) + 1
p('  非法命名: %d  股票数: %d' % (bad, len(ncode)))
p('  start_date 分布(top10):', sorted(starts.items(), key=lambda x: -x[1])[:10])
p('  end_date 分布(top10):', sorted(ends.items(), key=lambda x: -x[1])[:10])
p('  start_date 最早10个:', sorted(starts.keys())[:10])

# 抽样读取实际内容
random.seed(1)
samp = random.sample(files, min(8, len(files)))
p('')
p('抽样读取:')
for f in samp:
    try:
        d = pd.read_parquet(os.path.join(PD, f))
        cols = list(d.columns)
        td = d['trade_date'].astype(str) if 'trade_date' in d.columns else None
        p('  %-52s shape=%-12s cols=%s  td=%s~%s' % (
            f, d.shape, cols, td.min() if td is not None else '-', td.max() if td is not None else '-'))
    except Exception as e:
        p('  %-52s ERR %s' % (f, e))

# 专门找 2020 起始的文件
p('')
p('起始日为 20200101 的文件:')
g20 = sorted([f for f in files if '_20200101_' in f])
p('  数量: %d' % len(g20))
if g20:
    d = pd.read_parquet(os.path.join(PD, g20[0]))
    p('  例 %s shape=%s' % (g20[0], d.shape))
    p('  cols=%s' % list(d.columns))
    td = d['trade_date'].astype(str)
    p('  trade_date %s ~ %s' % (td.min(), td.max()))
    p(d.head(2).to_string())
    p(d.tail(2).to_string())

# daily_basic per-stock
p('')
b = [f for f in os.listdir(PD) if f.startswith('daily_basic_')]
p('daily_basic_* 文件数: %d' % len(b))
p('  例:', b[:3])
if b:
    d = pd.read_parquet(os.path.join(PD, b[0]))
    p('  shape=%s cols=%s' % (d.shape, list(d.columns)))
    td = d['trade_date'].astype(str)
    p('  trade_date %s ~ %s' % (td.min(), td.max()))

# treasure_daily per-stock
p('')
t = [f for f in os.listdir(PD) if f.startswith('treasure_daily_')]
p('treasure_daily_* 文件数: %d' % len(t))

with open(os.path.join(HERE, '_fs_probe9.txt'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(OUT))
print('DONE')
