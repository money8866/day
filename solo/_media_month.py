# -*- coding: utf-8 -*-
"""传媒主题：成员按 20 日（月）涨幅排序，标注 CORE/NORMAL/WEAK 与归属依据"""
import os
import sys
import numpy as np
import pandas as pd

BASE = r'd:\mystock\solo'
sys.path.insert(0, BASE)
os.chdir(BASE)

import theme_heat_v22 as H
from stock_cache import get_recent_trade_dates

LOG = open(os.path.join(BASE, '_media_month.log'), 'w', encoding='utf-8')
sys.stdout = LOG

THEME = '传媒'

td = H.resolve_trade_date(None)
dates = [str(d) for d in get_recent_trade_dates(n=H.HIST_DAYS, end_date=td)]
theme_members, map_path, _ = H.load_theme_members(td)
config = H.load_theme_config_v3()
mainbiz = H.load_mainbiz()
prev = H.load_prev_member_counts(td)
mv2, mv2_names = H.load_membership_v2()
mapping = H.apply_membership_v2(
    H.build_mapping_quality(theme_members, config, mainbiz, prev), mv2, config)

print('交易日', td, '| 主题键样例', list(theme_members.keys())[:3])
key = THEME if THEME in theme_members else None
if key is None:
    key = next((k for k in theme_members if THEME in k), None)
print('使用主题键:', key, '| 成员数', len(theme_members.get(key, {})))

recs = theme_members[key]
codes = sorted(recs.keys())
df = H.fetch_kline(codes, dates[0], dates[-1])
P, C, ACT = H.build_matrices(df, dates)
n = len(dates)
w = H.WIN_MONTH
base = C.iloc[n - w - 1]
last = C.iloc[-1]

lay = mapping[key]['layers']
rows = []
for c in codes:
    r = recs[c]
    lyr = lay.get(c, {}).get('layer', '')
    why = lay.get(c, {}).get('why', '')
    b, l = base.get(c, np.nan), last.get(c, np.nan)
    ret = (l / b - 1.0) * 100.0 if (pd.notna(b) and pd.notna(l) and b) else np.nan
    rows.append((c, r.get('name', ''), lyr, ret, r.get('via', ''), why, r.get('industry', '')))

rows.sort(key=lambda x: (-(x[3] if pd.notna(x[3]) else -999)))
print('\n窗口 = %s ~ %s（20 日）基线 %s\n' % (dates[n - w], dates[-1], dates[n - w - 1]))
print('%-11s %-8s %-7s %9s  %-22s %s' % ('code', 'name', 'layer', '20D%', 'via', '依据/行业'))
for c, nm, lyr, ret, via, why, ind in rows:
    print('%-11s %-8s %-7s %9s  %-22s %s' % (
        c, nm, lyr, ('%.2f' % ret) if pd.notna(ret) else 'NA', via, why or ind))

core = [r for r in rows if r[2] == 'CORE' and pd.notna(r[3])]
print('\n=== 仅 CORE（共 %d 只），按 20D 涨幅降序 ===' % len(core))
for c, nm, lyr, ret, via, why, ind in sorted(core, key=lambda x: -x[3])[:20]:
    print('  %-11s %-8s %+7.2f%%  %s' % (c, nm, ret, why or ind))

weak = sorted([r for r in rows if r[2] == 'WEAK'], key=lambda x: -(x[3] if pd.notna(x[3]) else -999))
print('\n=== WEAK 全量（共 %d 只） ===' % len(weak))
print('%-11s %-8s %9s  %-22s %-34s %s' % ('code', 'name', '20D%', 'via', 'why', '主营文本(前48字)'))
for c, nm, lyr, ret, via, why, ind in weak:
    txt = (mainbiz.get(c, '') or '').replace('\n', ' ').replace('\r', '')
    print('%-11s %-8s %9s  %-22s %-34s %s' % (
        c, nm, ('%.2f' % ret) if pd.notna(ret) else 'NA', via, why, txt[:48]))

from collections import Counter
print('\n--- WEAK 归因分布 ---')
for k, v in Counter(r[4] for r in weak).most_common():
    print('  %-22s %d' % (k, v))
print('--- WEAK 行业分布 ---')
for k, v in Counter(r[6] for r in weak).most_common():
    print('  %-16s %d' % (k, v))

LOG.close()
