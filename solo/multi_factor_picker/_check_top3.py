import json

with open(r'd:\mystock\solo\multi_factor_picker\output\wave2_theme_filtered_20260623.json','r',encoding='utf-8') as f:
    data = json.load(f)

# 检查主板前三
targets = ['600183.SH', '603929.SH', '603678.SH']
for t in targets:
    found = [r for r in data if r.get('ts_code') == t]
    if found:
        r = found[0]
        print(f"{t} {r.get('name','')}: score={r['score']} pattern={r['pattern']} confirmed={r.get('is_confirmed_theme')} active={r.get('is_currently_active_theme')} theme={r.get('best_theme','')}")
    else:
        print(f"{t}: NOT IN FILE")

# 统计
confirmed = [r for r in data if r.get('is_confirmed_theme')]
active = [r for r in data if r.get('is_currently_active_theme')]
dormant = [r for r in data if r.get('is_confirmed_theme') and not r.get('is_currently_active_theme')]
unconfirmed = [r for r in data if not r.get('is_confirmed_theme')]
print(f"\nTotal: {len(data)} | Confirmed: {len(confirmed)} | Active: {len(active)} | Dormant: {len(dormant)} | Unconfirmed: {len(unconfirmed)}")

# 主板confirmed列表
for r in sorted(dormant, key=lambda x: -x.get('score',0)):
    c = r.get('ts_code','')
    if c.startswith('600') or c.startswith('601') or c.startswith('603') or c.startswith('605'):
        print(f"  主板: {c} {r.get('name','')} score={r['score']} {r['pattern']} theme={r.get('best_theme','')}")
