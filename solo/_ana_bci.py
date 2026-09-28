# -*- coding: utf-8 -*-
"""临时：脑机接口当前成员池盘点。用毕即删。"""
import json, os
import theme_heat_v22 as th
import theme_mapping_v3_audit as A

CACHE = r'd:\mystock\cache_daily'
THEME = '脑机接口'
KEY = 'BRAIN_COMPUTER_INTERFACE'

members, map_path, raw = th.load_theme_members('20260924')
cfg = th.load_theme_config_v3()[THEME]
mainbiz = th.load_mainbiz()

recs = members.get(THEME) or {}
print(f'映射文件: {map_path}')
print(f'{THEME}: {len(recs)} 只')
print(f'人工名单: leaders={cfg.get("leaders")} core_stocks={cfg.get("core_stocks")}')

# 全量 stocks 侧：哪些股票的主题列表含脑机接口
stocks = raw.get('stocks') or {}
extra = [c for c, v in stocks.items() if THEME in (v.get('themes') or [])]
print(f'stocks 侧命中 {len(extra)} 只')

for code in sorted(recs):
    x = recs[code]
    txt = mainbiz.get(code, '') or ''
    lay, why = th.classify_member(code, x.get('via'), txt, cfg)
    r = A.classify(KEY, cfg, code, x.get('name', ''), x.get('via', ''),
                   x.get('industry', ''), txt)
    print(f"\n{code} {x.get('name','')} | {x.get('industry','')} | via={x.get('via','')}")
    print(f"   热量分层: {lay} ({why})")
    print(f"   审计判定: {r['classification']}/{r['evidence_type']} | {r['evidence'][:70]}")
    print(f"   主营: {txt[:110]}")
    print(f"   概念: {x.get('concepts') or x.get('concept') or ''}")
