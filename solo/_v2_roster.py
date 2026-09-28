# -*- coding: utf-8 -*-
"""V2.0 t2 末步：核对最终候选名单的名称 / 主营文本 / 现有主题归属"""
import os, json, sys

LOG = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '_v2_roster.log'),
           'w', encoding='utf-8')
sys.stdout = LOG

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = r'd:\mystock\cache_daily'

with open(os.path.join(CACHE, 'stock_company_mainbiz.json'), 'r', encoding='utf-8') as f:
    mainbiz = json.load(f)

names = {}
for p in (os.path.join(BASE, 'report_daily', 'theme_stock_map_latest_v2.json'),
          os.path.join(CACHE, 'theme_stock_map_v2_20260924.json'),
          os.path.join(CACHE, 'theme_stock_map_20260730.json'),
          os.path.join(CACHE, 'theme_stock_map_20260622.json')):
    if not os.path.exists(p):
        continue
    try:
        raw = json.load(open(p, 'r', encoding='utf-8'))
    except Exception:
        continue
    for c, v in (raw.get('stocks') or {}).items():
        if isinstance(v, dict) and v.get('name'):
            names.setdefault(c, v['name'])

cur = {}
for p in (os.path.join(BASE, 'report_daily', 'theme_stock_map_latest_v2.json'),
          os.path.join(CACHE, 'theme_stock_map_v2_20260924.json')):
    if not os.path.exists(p):
        continue
    try:
        raw = json.load(open(p, 'r', encoding='utf-8'))
    except Exception:
        continue
    for c, v in (raw.get('stocks') or {}).items():
        if isinstance(v, dict):
            cur.setdefault(c, v.get('themes') or [])
    break

GROUPS = {
    'AI服务器': ['002261.SZ', '000034.SZ', '001339.SZ', '000066.SZ', '603296.SH',
                 '300474.SZ', '600100.SH', '000628.SZ', '600839.SH', '688041.SH',
                 '688256.SH', '688802.SH', '688795.SH', '301202.SZ'],
    'CPO': ['688807.SH', '002222.SZ', '688048.SH', '688400.SH', '600498.SH'],
    '液冷': ['300249.SZ', '002335.SZ', '300602.SZ', '300684.SZ', '301489.SZ',
             '301626.SZ', '300647.SZ', '603124.SH', '002937.SZ', '002272.SZ'],
    '算力运营': ['002229.SZ', '600845.SH', '603003.SH', '002929.SZ', '600602.SH',
                 '603887.SH', '301085.SZ', '300017.SZ', '300895.SZ', '600589.SH',
                 '000815.SZ', '002575.SZ'],
    'AI应用': ['688327.SH', '688615.SH', '688207.SH', '688343.SH', '603859.SH',
               '601360.SH', '300766.SZ', '300451.SZ'],
}

for g, codes in GROUPS.items():
    print(f'\n===== {g} =====')
    for c in codes:
        nm = names.get(c, '???')
        th = ','.join(cur.get(c) or [])
        mb = (mainbiz.get(c) or '')[:140]
        print(f'{c} {nm:<8} [现有:{th}] | {mb}')

print(f'\nnames total={len(names)}')
LOG.close()
