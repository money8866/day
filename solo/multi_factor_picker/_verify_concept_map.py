# -*- coding: utf-8 -*-
"""验证概念缓存映射是否正确"""
import sys
sys.path.insert(0, '.')
import pandas as pd
from pathlib import Path

cache_dir = Path("cache")

# 读取概念列表
try:
    concepts_df = pd.read_parquet(cache_dir / "ths_concepts_list.parquet")
except:
    concepts_df = pd.read_csv(cache_dir / "ths_concepts_list.csv")
print(f"概念列表: {len(concepts_df)} 个")
print(f"概念列: {list(concepts_df.columns)}")

# 读取成分股
try:
    members_df = pd.read_parquet(cache_dir / "ths_concepts_members.parquet")
except:
    members_df = pd.read_csv(cache_dir / "ths_concepts_members.csv")
print(f"成分股: {len(members_df)} 条")
print(f"成分股列: {list(members_df.columns)}")

# 检查胜宏科技的概念映射
sheng = members_df[members_df['con_code'] == '300476.SZ']
print(f"\n胜宏科技(300476.SZ)所属概念({len(sheng)}个):")
for _, row in sheng.iterrows():
    ts_code = row['ts_code']
    con_name = row['concept_name']
    # 找到概念名称
    concept_name = concepts_df[concepts_df['ts_code'] == ts_code]['name'].values
    concept_name = concept_name[0] if len(concept_name) > 0 else ts_code
    print(f"  {concept_name}({ts_code})")

# 检查PCB概念包含哪些股票
pcb_code = '885959.TI'
pcb_stocks = members_df[members_df['ts_code'] == pcb_code]
print(f"\nPCB概念(885959.TI)成员({len(pcb_stocks)}只):")
for _, row in pcb_stocks.head(10).iterrows():
    print(f"  {row['con_code']}: {row['con_name']}")

# 检查"先进封装"概念
cf = members_df[members_df['concept_name'].str.contains('先进封装', na=False)]
print(f"\n先进封装概念成员({len(cf)}只):")
print(f"  概念代码: {cf['ts_code'].unique()}")
print(f"  概念名: {cf['concept_name'].unique()}")
for _, row in cf.head(5).iterrows():
    print(f"  {row['con_code']}: {row['con_name']}")
