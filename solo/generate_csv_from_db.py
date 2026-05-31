#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sqlite3
import pandas as pd
import os

db_path = "cache_backbone_tushare/theme_analysis.db"
csv_path = "cache_backbone_tushare/theme_portfolio_auto.csv"

conn = sqlite3.connect(db_path)
query = """
SELECT DISTINCT 
    theme_name as themes,
    ts_code,
    name
FROM leader_scores
ORDER BY theme_name, total_score DESC
"""
df = pd.read_sql_query(query, conn)
conn.close()

# Save to CSV
df.to_csv(csv_path, index=False, encoding='utf-8-sig')

print(f"✅ 已生成主题投资组合CSV: {csv_path}")
print(f"包含 {df['themes'].nunique()} 个主题, {len(df)} 只股票")
print(f"\n前5行:")
print(df.head())
