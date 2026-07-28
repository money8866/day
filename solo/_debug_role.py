#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""调试：输出北方长龙所在子主题的完整角色排名"""
import json
import sys

with open('d:/mystock/cache_daily/theme_stock_map_v2_20260727.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

code = '301357.SZ'

# 1. 北方长龙信息
s_out = data.get('stocks', {}).get(code, {})
sub = s_out.get('subtheme', '')
parent = (s_out.get('themes') or [''])[0]
print(f'北方长龙所属: 母主题="{parent}", 子主题="{sub}"')

# 2. 找到同子主题所有股票
same_sub = []
for c, info in data.get('stocks', {}).items():
    if info.get('subtheme') == sub and (info.get('themes') or [''])[0] == parent:
        same_sub.append(c)

print(f'\n同子主题共 {len(same_sub)} 只股票')

# 3. 按role_score排序输出
sorted_stocks = []
for c in same_sub:
    nm = data['stocks'][c].get('name', '')
    role = data.get('role_evolution', {}).get(c, {})
    sorted_stocks.append((c, nm, role))

# 按角色类型分组排序
role_order = {'Leader': 0, 'Core': 1, 'Momentum': 2, 'Beta': 3, 'Follower': 4, 'Defensive': 5, 'Weak': 6}
sorted_stocks.sort(key=lambda x: (role_order.get(x[2].get('role', 'Weak'), 9), -x[2].get('role_score', 0)))

print(f'\n{"="*120}')
print(f'{"股票代码":<12} {"名称":<10} {"角色":<10} {"角色分":>8} {"龙头相似度":>10} {"置信度":>8} {"特征":<60}')
print(f'{"="*120}')
for c, nm, r in sorted_stocks:
    role = r.get('role', 'N/A')
    rs = r.get('role_score', 0)
    ls = r.get('leader_similarity', 0)
    conf = r.get('confidence', 0)
    # 特征摘要
    feat = r.get('role_features', {})
    fs = f'str={feat.get("relative_strength",0):.2f} rec={feat.get("recognition",0):.2f} his={feat.get("historical_leader_prob",0):.2f} mom={feat.get("momentum_acceleration",0):.2f}'
    flag = '  ← 北方长龙' if c == code else ''
    print(f'{c:<12} {nm:<10} {role:<10} {rs:>8.3f} {ls:>10.3f} {conf:>8.2f} {fs:<60}{flag}')

# 4. 北方长龙特征明细
print(f'\n\n{"="*60}')
print(f'北方长龙特征明细')
print(f'{"="*60}')
role = data.get('role_evolution', {}).get(code, {})
feat = role.get('role_features', {})
for k, v in feat.items():
    print(f'  {k:<30} = {v:.3f}')
print(f'\n  all_role_scores (演化后):')
for k, v in role.get('all_role_scores', {}).items():
    print(f'    {k:<12} = {v:.4f}')
print(f'\n  final: role={role.get("role")} score={role.get("role_score")} ls={role.get("leader_similarity")}')
