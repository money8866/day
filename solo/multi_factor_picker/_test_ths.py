# -*- coding: utf-8 -*-
"""测试Tushare同花顺概念接口 (ths_index / ths_member)"""
import sys, os
sys.path.insert(0, '.')
import tushare as ts
import time
from main import load_config, get_token
from pathlib import Path

config = load_config()
token = get_token(config)
pro = ts.pro_api(token=token)

print("=" * 60)
print("测试 ths_index (同花顺概念板块列表)")
print("=" * 60)
try:
    concepts = pro.ths_index(exchange='A', type='N', fields='ts_code,name,count,list_date')
    print(f"✓ 获取 {len(concepts)} 个概念板块")
    print(f"  字段: {list(concepts.columns)}")
    print(f"  示例: {concepts.iloc[0].to_dict()}")

    # 找几个关键概念
    print("\n  关键概念查询:")
    for kw in ['AI', 'PCB', '算力', '机器人', '锂电池', '光伏', '半导体设备', 'HBM', '光模块', '低空', 'PCB', '先进封装']:
        matches = concepts[concepts['name'].str.contains(kw, na=False)]
        if len(matches) > 0:
            print(f"  '{kw}': {len(matches)}个")
            for _, r in matches.head(2).iterrows():
                print(f"    {r['ts_code']}: {r['name']} (count={r['count']})")
except Exception as e:
    print(f"✗ 失败: {e}")

# 2. ths_member
print("\n" + "=" * 60)
print("测试 ths_member (概念板块成分股)")
print("=" * 60)
try:
    # 取一个测试的ts_code
    test_code = concepts.iloc[0]['ts_code'] if len(concepts) > 0 else None
    if test_code:
        members = pro.ths_member(ts_code=test_code, fields='ts_code,con_code,con_name')
        print(f"✓ {test_code} 成分股 {len(members)} 只")
        print(f"  字段: {list(members.columns)}")
        print(members.head(5).to_string())
except Exception as e:
    print(f"✗ 失败: {e}")

# 3. 检查bak0615是否已有缓存
print("\n" + "=" * 60)
print("检查 bak0615 缓存")
print("=" * 60)
cache_path = Path("d:/mystock/solo/bak0615/cache_backbone_tushare/ths_concept_members.pkl")
if cache_path.exists():
    import pandas as pd
    df = pd.read_pickle(cache_path)
    print(f"✓ 缓存存在: {len(df)} 条, 概念{df['concept_name'].nunique()}个, 股票{df['con_code'].nunique()}只")
    print(df['concept_name'].value_counts().head(20).to_string())
else:
    print("✗ 缓存不存在")
