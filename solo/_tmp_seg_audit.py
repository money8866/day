# -*- coding: utf-8 -*-
"""环节清单门槛 dry-run 审计：量化被剔成员的「主营文本可用性」。

只用本地已生成文件，不联网、不修改任何数据。
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

P = r'd:\mystock\solo\report_daily\theme_segment_dropped_20260924.csv'

# 「文本不可用」启发式：经营范围样板 / 明显非主营业务描述
BOILER = ['投资兴办实业', '以上同类商品', '以上', '凭许可证', '依法须经批准',
          '法律法规', '经营范围', '国家有专项规定', '佣金代理', '进出口业务',
          '自有房屋租赁', '技术开发、技术咨询', '企业管理咨询']


def text_status(t):
    t = (t or '').strip()
    if not t:
        return 'EMPTY'
    if len(t) < 12:
        return 'TOOSHORT'
    if any(b in t for b in BOILER):
        return 'BOILERPLATE'
    return 'USABLE'


rows = list(csv.DictReader(io.open(P, encoding='utf-8-sig')))
print(f'被剔明细 {len(rows)} 条  <- {os.path.basename(P)}')
print()

cnt = collections.defaultdict(collections.Counter)
for r in rows:
    cnt[r['theme']][text_status(r['mainbiz'])] += 1

print('主题 | 被剔 | USABLE(真剔除) | BOILERPLATE | TOOSHORT | EMPTY')
for t in ['AI算力', '智能驾驶', '军工', '电力', '信创', '消费']:
    c = cnt[t]
    n = sum(c.values())
    print(f"{t:<6} | {n:>4} | {c['USABLE']:>6} | {c['BOILERPLATE']:>6} | "
          f"{c['TOOSHORT']:>6} | {c['EMPTY']:>5}")
tot = collections.Counter()
for c in cnt.values():
    tot.update(c)
print(f"合计   | {sum(tot.values()):>4} | {tot['USABLE']:>6} | {tot['BOILERPLATE']:>6} | "
      f"{tot['TOOSHORT']:>6} | {tot['EMPTY']:>5}")
print()

print('=' * 70)
print('各主题 USABLE（文本可用）被剔样本 —— 这些是「门槛真的判它不属于」的')
print('=' * 70)
for t in ['AI算力', '智能驾驶', '军工', '电力', '信创', '消费']:
    sub = [r for r in rows if r['theme'] == t and text_status(r['mainbiz']) == 'USABLE']
    print(f"\n【{t}】USABLE 被剔 {len(sub)} 条，前 25：")
    for r in sub[:25]:
        print(f"  {r['name']}({r['code']}) {r['industry']} via={r['via']} :: {r['mainbiz'][:60]}")

print()
print('=' * 70)
print('BOILERPLATE / TOOSHORT / EMPTY 被剔样本（应改为「不判、保留待复核」）')
print('=' * 70)
for t in ['AI算力', '军工', '消费']:
    sub = [r for r in rows if r['theme'] == t
           and text_status(r['mainbiz']) in ('BOILERPLATE', 'TOOSHORT', 'EMPTY')]
    print(f"\n【{t}】文本不可用被剔 {len(sub)} 条，前 15：")
    for r in sub[:15]:
        st = text_status(r['mainbiz'])
        print(f"  [{st}] {r['name']}({r['code']}) {r['industry']} :: {r['mainbiz'][:60]}")
