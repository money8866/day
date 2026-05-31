#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查龙头识别问题"""

import sqlite3

def check_leader_issue():
    """检查龙头识别问题"""
    print("="*60)
    print("检查龙头识别问题")
    print("="*60)
    
    # 连接数据库
    conn = sqlite3.connect('theme_portfolio.db')
    cursor = conn.cursor()
    
    # 1. 检查奕瑞科技出现在多少个主题中
    print("\n1. 检查奕瑞科技出现在多少个主题中...")
    cursor.execute("""
        SELECT DISTINCT theme_name 
        FROM theme_stocks 
        WHERE ts_code = '688301.SH'
    """)
    themes = cursor.fetchall()
    print(f"奕瑞科技出现在 {len(themes)} 个主题中:")
    for theme in themes:
        print(f"  - {theme[0]}")
    
    # 2. 检查每个主题的股票数量
    print("\n2. 检查每个主题的股票数量...")
    cursor.execute("""
        SELECT theme_name, COUNT(*) as stock_count 
        FROM theme_stocks 
        GROUP BY theme_name 
        ORDER BY stock_count DESC
        LIMIT 10
    """)
    theme_counts = cursor.fetchall()
    for theme, count in theme_counts:
        print(f"  {theme}: {count} 只股票")
    
    # 3. 检查股票代码映射
    print("\n3. 检查股票代码到名称的映射...")
    cursor.execute("""
        SELECT ts_code, name, COUNT(DISTINCT theme_name) as theme_count
        FROM theme_stocks
        GROUP BY ts_code
        ORDER BY theme_count DESC
        LIMIT 20
    """)
    stock_themes = cursor.fetchall()
    print("出现在最多主题中的股票:")
    for ts_code, name, count in stock_themes:
        print(f"  {name} ({ts_code}): {count} 个主题")
    
    # 4. 检查theme_stocks表的前10条记录
    print("\n4. 检查theme_stocks表的前10条记录...")
    cursor.execute("SELECT ts_code, name, theme_name FROM theme_stocks LIMIT 10")
    rows = cursor.fetchall()
    for ts_code, name, theme in rows:
        print(f"  {name} ({ts_code}) -> {theme}")
    
    conn.close()

if __name__ == "__main__":
    check_leader_issue()
