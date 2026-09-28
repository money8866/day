# -*- coding: utf-8 -*-
"""AI算力 拆旧建新：现状盘点"""
import json, os, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE = r'd:\mystock\solo'
MAP = os.path.join(BASE, 'report_daily', 'theme_stock_map_latest_v2.json')
CFG = os.path.join(BASE, 'theme_kg_v3', 'theme_kg_v3', 'config', 'theme_config.json')

with open(MAP, 'r', encoding='utf-8') as f:
    raw = json.load(f)
with open(CFG, 'r', encoding='utf-8') as f:
    cfg = json.load(f)

themes = raw.get('themes') or {}
print('=== 映射文件主题数: %d ===' % len(themes))
for t, lst in sorted(themes.items()):
    print('%-14s %d' % (t, len(lst)))

print()
print('=== config 主题 (key -> name_cn, is_active, level) ===')
for k, v in cfg.items():
    if k.startswith('_'):
        print('%-28s [META]' % k)
        continue
    print('%-28s %-12s active=%s level=%s' % (k, v.get('name_cn'), v.get('is_active'), v.get('level')))

print()
ai = themes.get('AI算力') or []
print('=== AI算力 成员 %d 只 ===' % len(ai))
print('%-12s %-10s %-20s %-14s %s' % ('code', 'name', 'via', 'industry', 'irs_layer'))
for s in ai:
    print('%-12s %-10s %-20s %-14s %s' % (
        s.get('code'), s.get('name'), s.get('via'), s.get('industry'), s.get('irs_layer')))

print()
print('=== 非AI算力主题中重复出现的同一股票（用于识别 SECONDARY 候选）===')
ai_codes = {s.get('code') for s in ai}
for t, lst in sorted(themes.items()):
    if t == 'AI算力':
        continue
    dup = [(s.get('code'), s.get('name')) for s in lst if s.get('code') in ai_codes]
    if dup:
        print('%-14s %d: %s' % (t, len(dup), dup))
