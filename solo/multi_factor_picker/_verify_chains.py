# -*- coding: utf-8 -*-
"""验证关键股票的概念归属"""
import sys
sys.path.insert(0, '.')
import pandas as pd
from pathlib import Path

cache_dir = Path("cache")
try:
    members_df = pd.read_parquet(cache_dir / "ths_concepts_members.parquet")
except:
    members_df = pd.read_csv(cache_dir / "ths_concepts_members.csv")

# 关键股票验证
for ts_code, name in [
    ('300476.SZ', '胜宏科技'),  # 应该是PCB链
    ('301377.SZ', '鼎泰高科'),  # 应该是AI PCB链
    ('600176.SH', '中国巨石'),  # 应该是PCB链（电子级玻纤）
    ('300395.SZ', '菲利华'),    # 半导体材料链
    ('600160.SH', '巨化股份'),  # 氟化工,不是AI算力
    ('300054.SZ', '鼎龙股份'),  # 半导体材料
    ('688127.SH', '蓝特光学'),  # 光学
    ('300503.SZ', '昊志机电'),  # 机器人
    ('300458.SZ', '全志科技'),  # 芯片设计
]:
    con = members_df[members_df['con_code'] == ts_code]
    print(f"\n{name}({ts_code}) 所属概念({len(con)}个):")
    for _, r in con.head(20).iterrows():
        print(f"  {r['concept_name']}")
