# -*- coding: utf-8 -*-
"""校验四个新主题 config 中 leaders / core_stocks 的代码-名称对应关系。"""
import json
import os
import sys

sys.path.insert(0, r'd:\mystock\solo')
from tushare_quant import pro  # noqa: E402

P = r'd:\mystock\solo\theme_kg_v3\theme_kg_v3\config\theme_config.json'
cfg = json.loads(open(P, 'rb').read().decode('utf-8-sig'))

keys = ['CPO', 'LIQUID_COOLING', 'AI_SERVER', 'AI_APPLICATION']
codes = []
for k in keys:
    codes += list(cfg[k].get('leaders') or []) + list(cfg[k].get('core_stocks') or [])
codes = sorted(set(codes))

df = pro.stock_basic(fields='ts_code,name,industry,market')
nmap = dict(zip(df['ts_code'], df['name']))
imap = dict(zip(df['ts_code'], df['industry']))
mmap = dict(zip(df['ts_code'], df['market']))

missing = [c for c in codes if c not in nmap]
print('=== 代码缺失 ===', missing if missing else 'none')
for k in keys:
    print('\n[%s] name_cn=%s' % (k, cfg[k]['name_cn']))
    for role in ('leaders', 'core_stocks'):
        for c in (cfg[k].get(role) or []):
            print('  %-6s %-12s %-10s %-16s %s' % (
                role, c, nmap.get(c, '<未知>'), imap.get(c, ''), mmap.get(c, '')))
