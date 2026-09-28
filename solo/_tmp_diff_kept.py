# -*- coding: utf-8 -*-
"""临时：对比 4a.47 口径同步前后，6 个 segment_gate 主题的成员增减（用完即删）"""
import csv, collections, os

RD = os.path.join('report_daily')

def load(p):
    with open(os.path.join(RD, p), 'r', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

pool = load('theme_pregate_pool_20260924.csv')
old_seg = load('_base_segdrop_20260924.csv')
old_pol = load('_base_poldrop_20260924.csv')
new_seg = load('theme_segment_dropped_20260924.csv')
new_pol = load('theme_pollution_dropped_20260924.csv')

def keyset(rows):
    return {(r['theme'], r['code']) for r in rows}

pool_k = keyset(pool)
os_, op_ = keyset(old_seg), keyset(old_pol)
ns_, np_ = keyset(new_seg), keyset(new_pol)

kept_old = pool_k - os_ - op_
kept_new = pool_k - ns_ - np_
added = kept_new - kept_old
removed = kept_old - kept_new

name = {}
for r in pool:
    name[(r['theme'], r['code'])] = r.get('name', '')

print('=== 口径同步前后（20260924，6 主题）===')
print(f'保留(旧) {len(kept_old)}  保留(新) {len(kept_new)}  新增 {len(added)}  移出 {len(removed)}')
print()
print('--- 新增成员（修复误剔）按主题 ---')
c = collections.Counter(t for t, _ in added)
for t, n in c.most_common():
    print(f'  {t}: {n}')
print()
print('--- 移出成员（修复误纳）按主题 ---')
c2 = collections.Counter(t for t, _ in removed)
for t, n in c2.most_common():
    print(f'  {t}: {n}')
print()
for label, s in (('新增', added), ('移出', removed)):
    print(f'--- {label}成员明细（≤60 条）---')
    for t, code in sorted(s)[:60]:
        print(f'  {t}\t{code}\t{name.get((t, code), "")}')
    print()

with open(os.path.join(RD, '_diff_kept_20260924.csv'), 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(['change', 'theme', 'code', 'name'])
    for t, code in sorted(added):
        w.writerow(['ADD', t, code, name.get((t, code), '')])
    for t, code in sorted(removed):
        w.writerow(['DEL', t, code, name.get((t, code), '')])
print('-> report_daily/_diff_kept_20260924.csv')
