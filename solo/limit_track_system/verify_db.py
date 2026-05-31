# -*- coding: utf-8 -*-
"""
验证SQLite数据库内容
"""

import sqlite3
import os

# 使用相对路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "cache", "limit_history.db")

print("="*80)
print("📊 SQLite数据库验证")
print("="*80)
print()

# 连接数据库
conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()

# 查询涨停数据表
print("【涨停数据表 - limit_stocks】")
print("-" * 80)
cursor.execute("SELECT COUNT(*) FROM limit_stocks")
total_count = cursor.fetchone()[0]
print(f"总记录数: {total_count}")

cursor.execute("""
    SELECT trade_date, COUNT(*) as count 
    FROM limit_stocks 
    GROUP BY trade_date 
    ORDER BY trade_date DESC
""")
print("\n每日涨停数量:")
for row in cursor.fetchall():
    print(f"  {row[0]}: {row[1]} 只")

# 查询最近10条记录
print("\n最近10条涨停记录:")
cursor.execute("""
    SELECT trade_date, ts_code, name, industry, close_price, vol_ratio
    FROM limit_stocks 
    ORDER BY trade_date DESC, id DESC
    LIMIT 10
""")
for row in cursor.fetchall():
    print(f"  {row[0]} | {row[2]} ({row[1]}) | {row[3]} | 收盘:{row[4]} | 量比:{row[5]}")

# 行业分布
print("\n行业分布统计:")
cursor.execute("""
    SELECT industry, COUNT(*) as count 
    FROM limit_stocks 
    GROUP BY industry 
    ORDER BY count DESC
    LIMIT 10
""")
for row in cursor.fetchall():
    print(f"  {row[0]}: {row[1]} 只")

# 查询分析数据表
print("\n" + "="*80)
print("【分析数据表 - stock_analysis】")
print("-" * 80)
cursor.execute("SELECT COUNT(*) FROM stock_analysis")
analysis_count = cursor.fetchone()[0]
print(f"总记录数: {analysis_count}")

if analysis_count > 0:
    print("\n最近分析记录:")
    cursor.execute("""
        SELECT trade_date, ts_code, name, wave2_prob
        FROM stock_analysis 
        ORDER BY trade_date DESC, wave2_prob DESC
        LIMIT 10
    """)
    for row in cursor.fetchall():
        print(f"  {row[0]} | {row[2]} ({row[1]}) | 二波概率: {row[3]}%")

conn.close()

print("\n" + "="*80)
print("✅ 数据库验证完成")
print("="*80)
