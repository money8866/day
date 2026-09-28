# -*- coding: utf-8 -*-
"""临时：脑机接口 修正后成员池 + 新旧对照。用毕即删。"""
import json
import theme_heat_v22 as th
import theme_mapping_v3_audit as A

THEME = '脑机接口'
KEY = 'BRAIN_COMPUTER_INTERFACE'
cfg = th.load_theme_config_v3()[THEME]
mainbiz = th.load_mainbiz()

old = json.load(open(r'd:\mystock\solo\_prev_bci_map.json', encoding='utf-8'))
new = json.load(open(r'd:\mystock\cache_daily\theme_stock_map_v2_20260924.json', encoding='utf-8'))

def side(raw):
    d = {x['code']: x for x in (raw['themes'].get(THEME) or [])}
    return d

o, n = side(old), side(new)
named = set(cfg.get('leaders') or []) | set(cfg.get('core_stocks') or [])
print(f'人工名单({len(named)}): {sorted(named)}')
print(f'修正前 {len(o)} 只 -> 修正后 {len(n)} 只')
print(f'新增: {sorted(set(n)-set(o))}')
print(f'移除: {sorted(set(o)-set(n))}\n')

def lay(code, x):
    return th.classify_member(code, x.get('via'), mainbiz.get(code, '') or '', cfg)

print('== 修正后成员 ==')
cnt = {'CORE': 0, 'NORMAL': 0, 'WEAK': 0}
for code in sorted(n):
    x = n[code]
    l, why = lay(code, x)
    cnt[l] += 1
    r = A.classify(KEY, cfg, code, x.get('name', ''), x.get('via', ''),
                   x.get('industry', ''), mainbiz.get(code, '') or '')
    print(f"{code} {x.get('name',''):<6} {x.get('industry',''):<6} {x.get('via',''):<16}"
          f" 热量={l:<6} 审计={r['classification']}/{r['evidence_type']}")
    print(f"    依据: {why}  | 审计依据: {r['evidence'][:60]}")
print(f"\n热量分层: {cnt}  有效成员(CORE+NORMAL) N = {cnt['CORE']+cnt['NORMAL']}")

# 被剔除的（原 13 只中消失的）
print('\n== 修正后消失的原成员 ==')
for code in sorted(set(o) - set(n)):
    x = o[code]
    r = A.classify(KEY, cfg, code, x.get('name', ''), x.get('via', ''),
                   x.get('industry', ''), mainbiz.get(code, '') or '')
    print(f"{code} {x.get('name',''):<6} via={x.get('via',''):<16} 审计={r['classification']}/{r['evidence_type']} | {r['evidence'][:56]}")
