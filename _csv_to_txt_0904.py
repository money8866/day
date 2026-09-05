# -*- coding: utf-8 -*-
import csv, io, sys, os

src = r'D:\mystock\solo\sli\output\sli_v2_subsector_top5_20260901.csv'
out = r'D:\mystock\solo\sli\output\sli_v2_subsector_top5_20260901.txt'

rows = []
with open(src, encoding='utf-8-sig') as f:
    rd = csv.DictReader(f)
    for r in rd:
        rows.append(r)

# 按三级行业分组，保持文件出现顺序
from collections import OrderedDict
groups = OrderedDict()
for r in rows:
    ind = r['三级行业']
    groups.setdefault(ind, []).append(r)

lines = []
lines.append('SLI_V2 细分赛道龙头 TOP5 榜单')
lines.append('生成日期: 2026-09-01  细分行业数: %d  标的数: %d' % (len(groups), len(rows)))
lines.append('=' * 100)
lines.append('')

legend = ('SLI_V2=综合评分  Product=产品力  Purity=纯度\n'
          '龙头类型: GROWTH_LEADER成长龙头 / PRODUCT_LEADER产品龙头 / PROFIT_LEADER盈利龙头 / NONE无\n'
          'Dominance: STRONG_LEADER强龙头 / (其他)  |  生命周期: ACCELERATING加速 / CONFIRMED确认 / ASCENDING上升 / DECLINING下滑')
lines.append(legend)
lines.append('-' * 100)

for ind, stocks in groups.items():
    lines.append('')
    lines.append('【%s】(%d只)' % (ind, len(stocks)))
    for s in stocks:
        sli = float(s['SLI_V2'])
        prod = float(s['Product'])
        pur = float(s['Purity'])
        lt = s['龙头类型']
        dm = s['Dominance']
        lc = s['生命周期']
        star = ' ★' if (lt != 'NONE' and sli >= 70) else ''
        lines.append('  %d. %s(%s)  SLI_V2=%.1f  Product=%.1f  Purity=%.1f  [%s/%s/%s]%s'
                     % (int(s['排名']), s['名称'], s['代码'], sli, prod, pur, lt, dm, lc, star))

with open(out, 'w', encoding='utf-8-sig') as f:
    f.write('\n'.join(lines))

print('生成成功:', out)
print('总行数:', len(lines), ' 文件大小:', os.path.getsize(out), 'bytes')
