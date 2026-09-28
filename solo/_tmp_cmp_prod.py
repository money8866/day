# -*- coding: utf-8 -*-
"""临时：正式映射落盘前后对比（backup csv vs new csv，均按 MAX_STOCKS_PER_THEME 截断）（用完即删）"""
import csv, os, collections

RD = 'report_daily'
OLD = os.path.join(RD, 'theme_stock_map_v2_20260924.csv.bak_p1p2')
NEW = os.path.join(RD, 'theme_stock_map_v2_20260924.csv')

def load(p):
    with open(p, 'r', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

old, new = load(OLD), load(NEW)
ko = {(r['主题'], r['股票代码']) for r in old}
kn = {(r['主题'], r['股票代码']) for r in new}

print(f'旧文件 {len(ko)} 条 / 新文件 {len(kn)} 条 / 交集 {len(ko & kn)}')
print(f'新增 {len(kn - ko)}  移出 {len(ko - kn)}')
print()
print('--- 新增 按主题 ---')
for t, n in collections.Counter(t for t, _ in (kn - ko)).most_common():
    print(f'  {t}: {n}')
print()
print('--- 移出 按主题 ---')
for t, n in collections.Counter(t for t, _ in (ko - kn)).most_common():
    print(f'  {t}: {n}')
print()
print('--- 分主题总数对比（旧→新）---')
co = collections.Counter(t for t, _ in ko)
cn = collections.Counter(t for t, _ in kn)
for t in sorted(set(co) | set(cn)):
    print(f'  {t}: {co.get(t,0)} -> {cn.get(t,0)}')
