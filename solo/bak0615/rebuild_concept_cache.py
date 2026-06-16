#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""重建概念缓存脚本"""
import os
import sys
import time
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
print("重建概念缓存")
print("=" * 60)

print("\n正在调用Tushare API获取同花顺概念板块数据...")
try:
    concept_df = pro.ths_index(exchange="A", type="N")
    time.sleep(0.1)
    if concept_df.empty:
        print("错误: 未获取到概念板块数据")
        sys.exit(1)
    print(f"共找到 {len(concept_df)} 个概念板块")
except Exception as e:
    print(f"调用Tushare API失败: {e}")
    sys.exit(1)

print("\n正在获取各概念板块的成份股...")
all_members = []
total = len(concept_df)

for idx, row in concept_df.iterrows():
    try:
        members = pro.ths_member(ts_code=row['ts_code'])
        if not members.empty:
            members['concept_name'] = row['name']
            all_members.append(members)
        time.sleep(0.05)
    except Exception as e:
        print(f"  获取概念 {row['name']} 成份股失败: {e}")
        continue

    if (idx + 1) % 50 == 0:
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