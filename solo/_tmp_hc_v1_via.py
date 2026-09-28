# -*- coding: utf-8 -*-
"""HC V1.0 辅助：映射来源(via) × 五状态 交叉统计 + 跨主题污染股 + 每主题来源构成"""
import csv, collections, os, sys

DATE = sys.argv[1] if len(sys.argv) > 1 else '20260924'
P = rf'd:\mystock\solo\report_daily\theme_mapping_hc_v1_{DATE}_members.csv'
rows = list(csv.DictReader(open(P, encoding='utf-8-sig')))
CLS = ['CORE', 'RELATED', 'WEAK', 'BAD', 'UNKNOWN']

print('=' * 100)
print(f'[via 交叉表] {DATE}  成员对={len(rows)}  唯一股票={len(set(r["code"] for r in rows))}')
print('=' * 100)
ct = collections.Counter((r['via'], r['cls']) for r in rows)
vias = sorted(set(r['via'] for r in rows), key=lambda v: -sum(ct[(v, c)] for c in CLS))
hdr = 'via'.ljust(24) + ''.join(c.rjust(9) for c in CLS) + 'TOTAL'.rjust(8) + 'BAD%'.rjust(8) + 'WEAKBAD%'.rjust(10)
print(hdr)
for v in vias:
    tot = sum(ct[(v, c)] for c in CLS)
    bad, weak = ct[(v, 'BAD')], ct[(v, 'WEAK')]
    print(v.ljust(24) + ''.join(str(ct[(v, c)]).rjust(9) for c in CLS)
          + str(tot).rjust(8) + f'{bad/tot*100:7.1f}%' + f'{(bad+weak)/tot*100:9.1f}%')

print()
print('=' * 100)
print('[跨主题污染股] 同一股票被 >=5 个主题纳入')
print('=' * 100)
st = collections.defaultdict(set)
nm = {r['code']: r['name'] for r in rows}
for r in rows:
    st[r['code']].add(r['theme'])
cross = sorted(((c, len(t)) for c, t in st.items() if len(t) >= 5), key=lambda x: -x[1])
print(f'合计 {len(cross)} 只（成员对中出现 {sum(n for _, n in cross)} 次）')
for c, n in cross[:40]:
    print(f'  {c} {nm.get(c, ""):<8} N={n:<3} {" / ".join(sorted(st[c]))}')

print()
print('=' * 100)
print('[每主题来源构成] 人工名单 vs 行业板块 vs 概念标签')
print('=' * 100)
MANUAL = {'leader_company', 'core_company', 'manual_override'}
INDUSTRY = {'dc_industry_board', 'stock_basic_industry'}
CONCEPT = {'concept_fallback', 'ths_concept', 'eastmoney_concept'}
tv = collections.defaultdict(collections.Counter)
for r in rows:
    tv[r['theme']][r['via']] += 1
print('theme'.ljust(12) + 'N'.rjust(5) + '人工'.rjust(6) + '行业板块'.rjust(9) + '概念'.rjust(6) + '其他'.rjust(6) + '   BAD行业来源Top3')
for th in sorted(tv, key=lambda t: -sum(tv[t].values())):
    c = tv[th]
    n = sum(c.values())
    m = sum(v for k, v in c.items() if k in MANUAL)
    i = sum(v for k, v in c.items() if k in INDUSTRY)
    k_ = sum(v for k, v in c.items() if k in CONCEPT)
    o = n - m - i - k_
    bad_ind = collections.Counter(r['industry'] for r in rows if r['theme'] == th and r['cls'] == 'BAD')
    top = ' '.join(f'{a}({b})' for a, b in bad_ind.most_common(3))
    print(f'{th:<12}{n:>5}{m:>6}{i:>9}{k_:>6}{o:>6}   {top}')

print()
print('=' * 100)
print('[BAD 全局行业来源 Top20]')
print('=' * 100)
bi = collections.Counter(r['industry'] for r in rows if r['cls'] == 'BAD')
for a, b in bi.most_common(20):
    print(f'  {a:<10} {b}')
print()
print('[WEAK 有文本 vs 无文本]')
wt = collections.Counter(('有文本' if r['has_text'] == '1' else '无文本') for r in rows if r['cls'] == 'WEAK')
print('  ', dict(wt))
print('[UNKNOWN 是否全部无文本]')
ut = collections.Counter(('有文本' if r['has_text'] == '1' else '无文本') for r in rows if r['cls'] == 'UNKNOWN')
print('  ', dict(ut))
print()
print('[UNKNOWN 的 via 分布]')
uv = collections.Counter(r['via'] for r in rows if r['cls'] == 'UNKNOWN')
for a, b in uv.most_common():
    print(f'  {a:<24} {b}')
