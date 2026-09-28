# -*- coding: utf-8 -*-
"""AI算力拆旧建新 —— 最终一致性 / 残留 / 污染检查"""
import io
import json
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE = r'd:\mystock\solo'
OLD = os.path.join(BASE, '_prev_map_ai.json')
MAP = os.path.join(BASE, 'report_daily', 'theme_stock_map_latest_v2.json')
CFG = os.path.join(BASE, 'theme_kg_v3', 'theme_kg_v3', 'config', 'theme_config.json')
HEAT_DIR = os.path.join(BASE, 'report_daily')

FOUR = ['CPO', '液冷', 'AI服务器', 'AI应用']   # config name_cn
FOUR_KEY = {'CPO': 'CPO', '液冷': 'LIQUID_COOLING', 'AI服务器': 'AI_SERVER', 'AI应用': 'AI_APPLICATION'}

old = json.load(open(OLD, encoding='utf-8'))
new = json.load(open(MAP, encoding='utf-8'))
cfg = json.load(open(CFG, encoding='utf-8'))

old_ai = {m['code']: m['name'] for m in old['themes']['AI算力']}
print('OLD_AI_COMPUTING_TOTAL = %d' % len(old_ai))

# ---------- 1. config 层残留 ----------
cfg_keys = [k for k in cfg.keys() if not k.startswith('_')]
cfg_names = []
for k in cfg_keys:
    v = cfg[k]
    if isinstance(v, dict):
        cfg_names.append(v.get('name_cn') or k)
print('\n[1] config 顶层主题 key 数 = %d ；含 _ 元数据 key = %d'
      % (len(cfg_keys), len([k for k in cfg.keys() if k.startswith('_')])))
print('    config 中 AI_COMPUTE 残留 = %s' % ('AI_COMPUTE' in cfg))
print('    config 中 name_cn=AI算力 残留 = %s' % ('AI算力' in cfg_names))
for cn, k in FOUR_KEY.items():
    ok = k in cfg and (cfg[k].get('name_cn') == cn)
    print('    四新主题 config 存在 %s(%s) = %s ; is_active=%s'
          % (cn, k, ok, cfg.get(k, {}).get('is_active')))

# ---------- 2. 映射层残留 ----------
new_themes = list(new['themes'].keys())
print('\n[2] 映射文件主题数 = %d' % len(new_themes))
print('    映射中 AI算力 残留 = %s' % ('AI算力' in new_themes))
PSEUDO = ['PCB高速互连', 'IDC', '算力电源', '智算运营', '光模块', 'CPO/光交换', '光芯片',
          'AI算力']
pseudo_hit = {p: len(new['themes'].get(p, [])) for p in PSEUDO if len(new['themes'].get(p, [])) > 0}
print('    伪主题(子主题名)残留 = %s' % (pseudo_hit if pseudo_hit else 'None'))
for cn in FOUR:
    ms = new['themes'].get(cn, [])
    print('    %-6s 成员 %2d 只 : %s' % (cn, len(ms), '/'.join(m['name'] for m in ms)))

# ---------- 3. 四新主题互斥 + 全市场一对一 PRIMARY ----------
sets = {t: {m['code'] for m in new['themes'].get(t, [])} for t in FOUR}
dup = []
for i in range(len(FOUR)):
    for j in range(i + 1, len(FOUR)):
        ov = sets[FOUR[i]] & sets[FOUR[j]]
        if ov:
            dup.append((FOUR[i], FOUR[j], sorted(ov)))
print('\n[3] 四新主题 pairwise 重叠 = %s' % (dup if dup else 'None'))

code_themes = {}
for t, ms in new['themes'].items():
    for m in ms:
        code_themes.setdefault(m['code'], []).append(t)
multi = {c: ts for c, ts in code_themes.items() if len(ts) > 1}
multi_new = {c: ts for c, ts in multi.items() if any(t in FOUR for t in ts)}
print('    全市场多主题股数 = %d（MAX_THEMES_PER_STOCK 允许多主题，非 PRIMARY 冲突）' % len(multi))
print('    新四主题股同时挂他主题 = %s'
      % {c: ts for c, ts in multi_new.items()})

# ---------- 4. 原 81 只落位 ----------
hit = {}
for t in FOUR:
    for c in sets[t]:
        hit.setdefault(c, []).append(t)
print('\n[4] 原 81 只 → 四新主题 PRIMARY 命中')
for cn in FOUR:
    mem = [c for c in sets[cn] if c in old_ai]
    print('    %-6s : %2d 只  %s' % (cn, len(mem), '/'.join(old_ai[c] for c in mem)))
assigned = sum(len([c for c in sets[cn] if c in old_ai]) for cn in FOUR)
print('    四新主题命中合计 = %d ; 待判 UNCERTAIN/EXCLUDE = %d'
      % (assigned, len(old_ai) - assigned))
print('\n    池外新增（非原 81 只）:')
for cn in FOUR:
    extra = [c for c in sets[cn] if c not in old_ai]
    nm = {}
    for m in new['themes'].get(cn, []):
        nm[m['code']] = m['name']
    print('      %-6s : %2d 只  %s' % (cn, len(extra), '/'.join(nm.get(c, c) for c in extra)))

# ---------- 5. 热度层 universe ----------
heat_files = sorted(f for f in os.listdir(HEAT_DIR)
                    if re.fullmatch(r'theme_heat_v24_\d{8}\.json', f))
print('\n[5] 热度文件 v24 = %d 个 ; 最新 = %s'
      % (len(heat_files), heat_files[-1] if heat_files else 'None'))
if heat_files:
    h = json.load(open(os.path.join(HEAT_DIR, heat_files[-1]), encoding='utf-8'))
    names = [r.get('theme') for r in h] if isinstance(h, list) else []
    print('    最新热度文件主题行数 = %d' % len(names))
    print('    热度 universe 含 AI算力 = %s' % ('AI算力' in names))
    for cn in FOUR:
        row = next((r for r in h if r.get('theme') == cn), None) if isinstance(h, list) else None
        if row is None:
            print('    热度 universe 含 %-6s = False' % cn)
        else:
            print('    热度 %-6s : in_universe=%s n_map=%s TODAY rank=%s heat=%s | WEEK rank=%s heat=%s | MONTH rank=%s heat=%s'
                  % (cn, row.get('in_universe'), row.get('n_map'),
                     row.get('today_rank'), row.get('today_heat'),
                     row.get('week_rank'), row.get('week_heat'),
                     row.get('month_rank'), row.get('month_heat')))

# ---------- 6. SECONDARY 候选 ----------
print('\n[6] 原 81 只在他主题中重复出现（SECONDARY 候选，不参与热度统计）')
for t, ms in new['themes'].items():
    if t in FOUR:
        continue
    inter = [(m['code'], m['name']) for m in ms if m['code'] in old_ai]
    if inter:
        print('    %-8s %2d: %s' % (t, len(inter), inter))
