# -*- coding: utf-8 -*-
"""V2.0 映射重跑差异核对：AI5 增删 + G12（非AI5主题零变化）"""
import json
import os
import sys
from collections import defaultdict

BASE = r'd:\mystock\solo'
BD = os.path.join(BASE, 'report_daily')
AI5 = ('CPO', '液冷', 'AI服务器', '算力运营', 'AI应用')

LOG = open(os.path.join(BASE, '_v2_map_diff.log'), 'w', encoding='utf-8')
sys.stdout = LOG

base = json.load(open(os.path.join(BASE, '_v2_map_baseline.json'), encoding='utf-8'))
new = json.load(open(os.path.join(BD, 'theme_stock_map_latest_v2.json'), encoding='utf-8'))
newt = {t: [s['code'] for s in lst] for t, lst in new['themes'].items()}

print('trade_date = %s | update_time = %s' % (new.get('trade_date'), new.get('update_time')))
print('基线 主题%d 映射%d | 新 主题%d 映射%d 个股%d'
      % (len(base), sum(len(v) for v in base.values()),
         len(newt), sum(len(v) for v in newt.values()), new.get('n_stocks', 0)))

print('\n=== 各主题变化（仅列有变化的）===')
for t in sorted(set(base) | set(newt)):
    b, a = set(base.get(t, [])), set(newt.get(t, []))
    if b != a:
        tag = '[AI5]' if t in AI5 else '[OTHER]'
        print('%-14s %s  %d -> %d  +%s  -%s'
              % (t, tag, len(b), len(a), sorted(a - b), sorted(b - a)))

print('\n=== G12 非 AI5 主题核对 ===')
bad = []
for t in sorted(set(base) | set(newt)):
    if t in AI5:
        continue
    if set(base.get(t, [])) != set(newt.get(t, [])):
        bad.append(t)
print('非AI5主题数量: %d | 发生变化: %d %s'
      % (len([t for t in set(base) | set(newt) if t not in AI5]), len(bad), bad))

# 个股维度
old_map, new_map = defaultdict(set), defaultdict(set)
for t, cs in base.items():
    for c in cs:
        old_map[c].add(t)
for t, cs in newt.items():
    for c in cs:
        new_map[c].add(t)

print('\n=== 个股 themes 集合变化 ===')
n_gain, n_lose = 0, 0
for c in sorted(set(old_map) | set(new_map), key=lambda x: -len(new_map[x] - old_map[x])):
    o, n = old_map[c], new_map[c]
    if o == n:
        continue
    add, rm = sorted(n - o), sorted(o - n)
    if add:
        n_gain += 1
    if rm:
        n_lose += 1
    print('%s  %s -> +%s  -%s' % (c, sorted(o), add, rm))
print('获得新主题的个股: %d | 丢失主题的个股: %d' % (n_gain, n_lose))

print('\n=== AI5 映射成员终值 ===')
for t in AI5:
    lst = new['themes'].get(t, [])
    print('%-8s %d  %s' % (t, len(lst), [s['code'] for s in lst]))

print('\nDONE')
LOG.close()
