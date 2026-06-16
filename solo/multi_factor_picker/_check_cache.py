# -*- coding: utf-8 -*-
"""检查缓存文件内容"""
import sys
sys.path.insert(0, '.')
import pandas as pd
from pathlib import Path

cache_dir = Path("cache")
print("缓存目录内容:")
for f in cache_dir.glob("ths_*"):
    print(f"  {f.name}: {f.stat().st_size} bytes")

# 读取members缓存（兼容csv/parquet）
members_csv = cache_dir / "ths_concepts_members.csv"
if members_csv.exists():
    df = pd.read_csv(members_csv)
    print(f"\nMembers缓存(CSV): {len(df)}行")
    print(f"  字段: {list(df.columns)}")
    print(f"  样例: {df.head(3).to_dict('records')}")
    # 检查胜宏科技
    sheng = df[df['con_name'].str.contains('胜宏', na=False)]
    print(f"  胜宏科技相关({len(sheng)}条):")
    print(sheng.to_string())
