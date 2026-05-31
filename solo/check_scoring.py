#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查主题评分问题"""

import sys
import os
import sqlite3
import pandas as pd
import numpy as np

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入必要的模块
from theme_rotation_analysis_final import (
    load_theme_portfolio_from_sqlite,
    identify_theme_leaders,
    get_stock_history,
    calculate_comprehensive_leader_score
)

def diagnose_scoring():
    """诊断评分问题"""
    print("="*60)
    print("诊断主题评分问题")
    print("="*60)
    
    # 1. 加载主题投资组合
    print("\n1. 加载主题投资组合...")
    theme_stocks_map, name_map = load_theme_portfolio_from_sqlite()
    print(f"加载了 {len(theme_stocks_map)} 个主题")
    
    # 2. 测试单只股票的评分计算
    print("\n2. 测试单只股票的评分计算...")
    test_stock = list(name_map.keys())[0]
    test_name = name_map[test_stock]
    print(f"测试股票: {test_name} ({test_stock})")
    
    # 获取股票历史数据
    print(f"  获取历史数据...")
    df = get_stock_history(test_stock, 25)
    print(f"  历史数据行数: {len(df)}")
    
    if not df.empty:
        print(f"  最新数据日期: {df.iloc[-1]['trade_date']}")
        print(f"  最新涨跌幅: {df.iloc[-1]['pct_chg']}%")
    else:
        print(f"  ❌ 无法获取历史数据")
    
    # 计算评分
    print(f"  计算综合评分...")
    result = calculate_comprehensive_leader_score(test_stock, name_map)
    
    if result is not None:
        print(f"  ✅ 评分计算成功")
        print(f"     total_score: {result['total_score']}")
        print(f"     5日涨幅: {result['change_5']:.2f}%")
        print(f"     20日涨幅: {result['change_20']:.2f}%")
    else:
        print(f"  ❌ 评分计算失败，返回None")
    
    # 3. 测试主题龙头识别
    print("\n3. 测试主题龙头识别...")
    first_theme = list(theme_stocks_map.keys())[0]
    first_stocks = theme_stocks_map[first_theme]
    print(f"主题: {first_theme}")
    print(f"股票数量: {len(first_stocks)}")
    print(f"前5个股票: {first_stocks[:5]}")
    
    print(f"正在识别龙头...")
    leaders = identify_theme_leaders(first_stocks, name_map)
    print(f"识别到的龙头数量: {len(leaders)}")
    
    if leaders:
        print(f"龙头评分:")
        for i, leader in enumerate(leaders[:5]):
            print(f"  {i+1}. {leader['name']} ({leader['ts_code']}): {leader['total_score']:.1f}")
    else:
        print(f"  ⚠️ 未识别到任何龙头")
    
    # 4. 检查主题评分
    print("\n4. 检查主题评分...")
    theme_scores = {}
    for theme_name, theme_stocks in list(theme_stocks_map.items())[:5]:
        leaders = identify_theme_leaders(list(theme_stocks), name_map)
        if leaders:
            theme_scores[theme_name] = np.mean([l['total_score'] for l in leaders])
        else:
            theme_scores[theme_name] = 0
    
    print(f"主题评分:")
    for theme, score in theme_scores.items():
        print(f"  {theme}: {score:.1f}")

if __name__ == "__main__":
    diagnose_scoring()
