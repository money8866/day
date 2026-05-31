#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""快速重建概念缓存 - 只获取主题相关概念"""
import os, sys, json, time
import pandas as pd
import tushare as ts
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN)

CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")
os.makedirs(CACHE_DIR, exist_ok=True)

print("=" * 60)
print("快速重建概念缓存（简化版）")
print("=" * 60)

# 加载 theme.json 获取主题相关的概念名称
json_path = os.path.join(BASE_DIR, "..", "theme.json")
with open(json_path, 'r', encoding='utf-8') as f:
    data = json.load(f)
themes = data.get('HOT_THEMES', {})

# 收集所有需要的概念名称
needed_concepts = set()
for theme_name, theme_data in themes.items():
    needed_concepts.update(theme_data.get('concept', []))
    needed_concepts.update(theme_data.get('keywords', []))

print(f"从 theme.json 中找到 {len(needed_concepts)} 个相关概念/关键词")
print(f"概念示例: {list(needed_concepts)[:10]}")

# 获取所有概念板块
print("\n获取所有概念板块...")
concept_df = pro.ths_index(exchange="A", type="N")
print(f"共找到 {len(concept_df)} 个概念板块")

# 筛选出需要的概念板块
target_concepts = []
for _, row in concept_df.iterrows():
    if row['name'] in needed_concepts:
        target_concepts.append(row)

print(f"匹配到 {len(target_concepts)} 个目标概念板块")

# 如果没有精确匹配，尝试模糊匹配
if len(target_concepts) < 10:
    print("\n尝试模糊匹配...")
    for _, row in concept_df.iterrows():
        for kw in needed_concepts:
            if kw in row['name'] or row['name'] in str(kw):
                if row not in target_concepts:
                    target_concepts.append(row)

print(f"模糊匹配后共 {len(target_concepts)} 个概念板块")

if not target_concepts:
    print("警告: 没有找到匹配的概念，使用全部概念板块")
    target_concepts = concept_df.to_dict('records')

# 获取目标概念的成份股
print("\n获取概念成份股...")
all_members = []
total = len(target_concepts)

for idx, row in enumerate(target_concepts):
    try:
        members = pro.ths_member(ts_code=row['ts_code'])
        if not members.empty:
            members['concept_name'] = row['name']
            all_members.append(members)
        time.sleep(0.05)
    except Exception as e:
        print(f"  获取概念 {row['name']} 失败: {e}")
        continue

    if (idx + 1) % 20 == 0:
        print(f"  已处理 {idx + 1}/{total} 个概念板块")

if not all_members:
    print("错误: 未获取到任何概念成份股数据")
    sys.exit(1)

df = pd.concat(all_members, ignore_index=True)
cache_file = os.path.join(CACHE_DIR, "ths_concept_members.pkl")
df.to_pickle(cache_file)

print(f"\n成功保存 {len(df)} 条概念成份股记录到缓存")
print(f"缓存路径: {cache_file}")

# 验证缓存
df_check = pd.read_pickle(cache_file)
print(f"验证: 读取缓存 {len(df_check)} 条记录")
print(f"概念数量: {df_check['concept_name'].nunique()}")
print(f"股票数量: {df_check['con_code'].nunique()}")

print("\n概念列表示例:")
print(df_check['concept_name'].value_counts().head(10).to_string())

print("\n" + "=" * 60)
print("概念缓存重建完成！")
print("=" * 60)