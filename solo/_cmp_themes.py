#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""对比 theme_config.json 与 subtheme_map.json 的母主题"""
import json

tc = 'd:/mystock/solo/theme_kg_v3/theme_kg_v3/config/theme_config.json'
sc = 'd:/mystock/solo/theme_kg_v3/theme_kg_v3/config/subtheme_map.json'

with open(tc, 'r', encoding='utf-8') as f:
    cfg = json.load(f)
with open(sc, 'r', encoding='utf-8') as f:
    sub = json.load(f)

print("theme_config.json 母主题 (key: name_cn):")
tc_names = {}
for k, v in cfg.items():
    if k.startswith('_'):
        continue
    tc_names[v.get('name_cn', k)] = k
    print(f"  {v.get('name_cn', k):<12} key={k}")

print(f"\nsubtheme_map.json 母主题 ({len(sub)}个):")
for p in sub:
    subs = list(sub[p].keys())
    print(f"  {p:<12} 子主题数={len(subs)}  {subs[:6]}...")

# 对比
print("\n两者对比: 只在theme_config有 / 只在subtheme_map有:")
only_tc = set(tc_names.keys()) - set(sub.keys())
only_sub = set(sub.keys()) - set(tc_names.keys())
print(f"  只在 theme_config: {only_tc if only_tc else '无'}")
print(f"  只在 subtheme_map: {only_sub if only_sub else '无'}")
