#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""获取申万行业和同花顺概念板块的准确名称"""
import tushare as ts
from dotenv import load_dotenv
import os
import json

load_dotenv('d:/mystock/config/.env')
pro = ts.pro_api(os.getenv('TUSHARE_TOKEN'))

print("=" * 60)
print("申万行业数据")
print("=" * 60)

# 获取申万行业分类
try:
    sw_df = pro.index_classify(src='SW2021', level='L1')
    print(f"\n申万一级行业({len(sw_df)}个):")
    l1_set = set()
    for _, r in sw_df.iterrows():
        l1_set.add(r['industry_name'])
    for name in sorted(l1_set):
        print(f"  {name}")
except Exception as e:
    print(f"获取失败: {e}")

print()
print("=" * 60)
print("同花顺概念板块数据")
print("=" * 60)

ths_df = pro.ths_index(exchange="A", type="N")
print(f"\n同花顺概念板块总数: {len(ths_df)}")

# 按字母顺序分组显示
by_letter = {}
for _, r in ths_df.iterrows():
    name = r['name']
    first_char = name[0] if name else '#'
    if first_char.isalpha():
        first_char = first_char.upper()
    else:
        first_char = '#'
    if first_char not in by_letter:
        by_letter[first_char] = []
    by_letter[first_char].append(name)

for letter in sorted(by_letter.keys()):
    print(f"\n{letter}:")
    for name in sorted(by_letter[letter]):
        print(f"  {name}")