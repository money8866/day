# -*- coding: utf-8 -*-
"""环节清单门槛 vs HC V1.0 判定 交叉验证。

问题：门槛剔除的 1297 条，是否与 HC 认定的污染（BAD/WEAK）一致？
若一致 → 门槛有效；若门槛大量删掉 HC 认定的 CORE/RELATED → 过度剔除。
只用本地文件，不联网、不改数据。
"""
import csv
import io
import collections
import os
import sys

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

D = r'd:\mystock\solo\report_daily'
SEG = os.path.join(D, 'theme_segment_dropped_20260924.csv')
MEM = os.path.join(D, 'theme_mapping_hc_v1_20260924_members.csv')

THEMES = ['AI算力', '智能驾驶', '军工', '电力', '信创', '消费']


def load(p):
    return list(csv.DictReader(io.open(p, encoding='utf-8-sig')))


seg = load(SEG)
mem = load(MEM)

key = lambda r: (r['theme'], r['code'])
hc = {key(r): r for r in mem}

print(f'门槛剔除 {len(seg)} 条；HC members {len(mem)} 条；主题限定 {THEMES}')
print()

# HC 全体分布（限定 6 主题）
print('【HC V1.0 在 6 主题内的判定分布】')
c0 = collections.Counter(r['cls'] for r in mem if r['theme'] in THEMES)
print('  ' + '  '.join(f'{k}={v}' for k, v in c0.most_common()))
print()

# 交叉表
print('【门槛剔除 × HC 判定】')
tab = collections.defaultdict(collections.Counter)
miss = 0
for r in seg:
    m = hc.get(key(r))
    if m is None:
        tab[r['theme']]['NOT_IN_HC'] += 1
        miss += 1
        continue
    tab[r['theme']][m['cls']] += 1

cols = ['CORE', 'RELATED', 'WEAK', 'BAD', 'UNKNOWN', 'NOT_IN_HC']
print('主题     | ' + ' | '.join(f'{c:>9}' for c in cols))
tot = collections.Counter()
for t in THEMES:
    c = tab[t]
    tot.update(c)
    print(f'{t:<7}| ' + ' | '.join(f'{c[c2]:>9}' for c2 in cols))
print(f'{"合计":<6}| ' + ' | '.join(f'{tot[c]:>9}' for c in cols))
print()
print(f'未在 HC members 中的剔除 {miss} 条（HC members 只含 6 主题？需核对）')
print()

# 严重误删：被门槛剔除但 HC 判 CORE / RELATED
print('=' * 72)
print('疑似误删：门槛剔除 但 HC 判 CORE / RELATED')
print('=' * 72)
for t in THEMES:
    bad = [r for r in seg if r['theme'] == t
           and hc.get(key(r), {}).get('cls') in ('CORE', 'RELATED')]
    print(f'\n【{t}】{len(bad)} 条')
    for r in bad[:20]:
        m = hc[key(r)]
        print(f"  [{m['cls']}] {r['name']}({r['code']}) {r['industry']} "
              f":: {r['mainbiz'][:50]}")
