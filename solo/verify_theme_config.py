#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证 theme.json 中的 industry/concept 是否与 Tushare 接口匹配"""
import os
import json
import tushare as ts
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "config", ".env"))
token = os.getenv("TUSHARE_TOKEN")
if not token:
    raise SystemExit("ERROR: TUSHARE_TOKEN not set")
pro = ts.pro_api(token)

THEME_FILE = os.path.join(os.path.dirname(__file__), "theme.json")

with open(THEME_FILE, "r", encoding="utf-8") as f:
    theme_data = json.load(f)
hot_themes = theme_data.get("HOT_THEMES", {})

theme_industries = set()
theme_concepts = set()
theme_industry_by_theme = {}
theme_concept_by_theme = {}

for theme_name, cfg in hot_themes.items():
    inds = cfg.get("industry", [])
    cons = cfg.get("concept", [])
    theme_industries.update(inds)
    theme_concepts.update(cons)
    theme_industry_by_theme[theme_name] = inds
    theme_concept_by_theme[theme_name] = cons

print(f"主题数: {len(hot_themes)}")
print(f"配置中唯一行业数: {len(theme_industries)}")
print(f"配置中唯一概念数: {len(theme_concepts)}")

# 申万行业
print("\n=== 获取申万行业 (index_member_all) ===")
sw_df = pro.index_member_all(is_new="Y")
print(f"申万成分记录数: {len(sw_df)}")
print(f"列名: {list(sw_df.columns)}")

sw_names = set()
for col in ["l1_name", "l2_name", "l3_name"]:
    if col in sw_df.columns:
        sw_names.update(sw_df[col].dropna().unique())

print(f"申万行业名称总数(l1+l2+l3去重): {len(sw_names)}")

missing_ind = sorted(theme_industries - sw_names)
matched_ind = sorted(theme_industries & sw_names)
print(f"\n匹配的行业: {len(matched_ind)}/{len(theme_industries)}")
if missing_ind:
    print("未匹配的行业:")
    for m in missing_ind:
        close = [
            n
            for n in sw_names
            if m.replace("Ⅱ", "") in n
            or n.replace("Ⅱ", "") in m
            or m[:2] in n
        ]
        print(f"  [{m}]  近似: {close[:5]}")
else:
    print("所有行业均匹配!")

# 同花顺概念
print("\n=== 获取同花顺概念 (ths_index type=N) ===")
concept_df = pro.ths_index(exchange="A", type="N")
ths_names = set(concept_df["name"].dropna().unique())
print(f"同花顺概念总数: {len(ths_names)}")

missing_con = sorted(theme_concepts - ths_names)
matched_con = sorted(theme_concepts & ths_names)
print(f"\n匹配的概念: {len(matched_con)}/{len(theme_concepts)}")
if missing_con:
    print("未匹配的概念:")
    for m in missing_con:
        close = [
            n
            for n in ths_names
            if m[:3] in n or n[:3] in m or m.replace("(", "").replace(")", "") in n
        ]
        close = sorted(set(close))[:8]
        print(f"  [{m}]  近似: {close}")
else:
    print("所有概念均匹配!")

print("\n=== 各主题未匹配项 ===")
has_issue = False
for theme_name in sorted(hot_themes.keys()):
    bad_ind = [i for i in theme_industry_by_theme[theme_name] if i not in sw_names]
    bad_con = [c for c in theme_concept_by_theme[theme_name] if c not in ths_names]
    if bad_ind or bad_con:
        has_issue = True
        print(f"{theme_name}:")
        if bad_ind:
            print(f"  行业未匹配: {bad_ind}")
        if bad_con:
            print(f"  概念未匹配: {bad_con}")

if not has_issue:
    print("(无)")
