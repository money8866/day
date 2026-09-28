# -*- coding: utf-8 -*-
"""临时：脑机接口 55 只概念成员逐只诊断（为何被判出/留下）。用毕即删。"""
import json, os
import theme_heat_v22 as th
import theme_mapping_v3_audit as A
import theme_trend_sentiment_score as ts
from tushare_quant import pro

THEME = '脑机接口'
KEY = 'BRAIN_COMPUTER_INTERFACE'
cfg = th.load_theme_config_v3()[THEME]
mainbiz = th.load_mainbiz()

dc = ts.get_dc_members()
members = sorted(set(dc[dc['concept_name'] == THEME]['con_code']))

basic = pro.stock_basic(fields='ts_code,name,industry')
name_map = dict(zip(basic['ts_code'], basic['name']))
ind_map = dict(zip(basic['ts_code'], basic['industry']))

cur = json.load(open(r'd:\mystock\cache_daily\theme_stock_map_v2_20260924.json', encoding='utf-8'))
in_map = {x['code']: x for x in (cur['themes'].get(THEME) or [])}
stk_side = {c for c, v in (cur.get('stocks') or {}).items() if THEME in (v.get('themes') or [])}

named = set(cfg.get('leaders') or []) | set(cfg.get('core_stocks') or [])
print(f'概念成员 {len(members)} 只｜当前映射 {len(in_map)} 只｜stocks 侧 {len(stk_side)} 只')
print(f'人工名单: {sorted(named)}\n')

stat = {}
for c in members:
    nm = name_map.get(c, '')
    ind = ind_map.get(c, '')
    mb = mainbiz.get(c, '') or ''
    lay, why = th.classify_member(c, '', mb, cfg)
    r = A.classify(KEY, cfg, c, nm, '', ind, mb)
    tag = 'IN' if c in in_map else '--'
    key = r['classification']
    stat[key] = stat.get(key, 0) + 1
    print(f"{tag} {c} {nm:<6} {ind or '':<6} {lay:<6} {r['classification']}/{r['evidence_type']:<4} | {r['evidence'][:44]}")
    print(f"      主营: {mb[:88]}")

print('\n判定分布:', stat)
print('在映射但不在概念 55 只内:', sorted(set(in_map) - set(members)))
