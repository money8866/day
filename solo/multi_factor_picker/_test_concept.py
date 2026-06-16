# -*- coding: utf-8 -*-
"""测试Tushare概念板块接口可用性"""
import sys, os
sys.path.insert(0, '.')
import tushare as ts
from main import load_config, get_token
from datetime import datetime

config = load_config()
token = get_token(config)
pro = ts.pro_api(token=token)

print("测试Tushare概念接口...")
print("=" * 60)

# 1. 概念列表
try:
    concepts = pro.concept(src='ts')
    print(f"\n✓ concept() 返回 {len(concepts)} 个概念")
    print(f"  字段: {list(concepts.columns)}")
    print(f"  示例: {concepts.iloc[0].to_dict()}")
    # 找几个AI/PCB相关概念
    for kw in ['AI', 'PCB', '算力', '机器人', '新能源', '半导体设备', '锂电池', '光伏']:
        matches = concepts[concepts['name'].str.contains(kw, na=False)]
        if len(matches) > 0:
            print(f"  含'{kw}'的概念:")
            for _, r in matches.head(3).iterrows():
                print(f"    {r['code']}: {r['name']}")
except Exception as e:
    print(f"  ✗ concept() 失败: {e}")

# 2. 概念详情
try:
    # 取一个测试code
    test_code = 'TS2'  # 数字经济
    detail = pro.concept_detail(id=test_code, fields='id,concept_name,ts_code,name')
    if len(detail) > 0:
        print(f"\n✓ concept_detail(id='{test_code}') 返回 {len(detail)} 条")
        print(f"  字段: {list(detail.columns)}")
        print(f"  前5条: {detail.head(5).to_dict('records')}")
except Exception as e:
    print(f"  ✗ concept_detail() 失败: {e}")
