#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""快速测试calculate_comprehensive_leader_score"""

import sys
import os

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入必要的模块
from theme_rotation_analysis_final import (
    load_theme_portfolio_from_sqlite,
    calculate_theme_historical_rankings,
    calculate_comprehensive_leader_score,
    get_stock_history,
    get_trade_dates,
    identify_theme_leaders
)

def quick_test():
    """快速测试"""
    print("="*60)
    print("快速测试")
    print("="*60)
    
    # 1. 加载主题投资组合
    print("\n1. 加载主题投资组合...")
    theme_stocks_map, name_map = load_theme_portfolio_from_sqlite()
    first_theme = list(theme_stocks_map.keys())[0]
    first_stocks = theme_stocks_map[first_theme]
    print(f"测试主题: {first_theme}")
    print(f"股票列表: {first_stocks[:3]}")
    
    # 2. 模拟主程序的调用顺序
    print("\n2. 模拟主程序的调用顺序...")
    trade_dates = ['20260529']
    
    # 3. 先调用calculate_theme_historical_rankings
    print("\n3. 调用calculate_theme_historical_rankings...")
    theme_summary = calculate_theme_historical_rankings(theme_stocks_map, trade_dates)
    print(f"计算完成，共 {len(theme_summary)} 个主题")
    
    # 4. 测试calculate_comprehensive_leader_score
    print(f"\n4. 测试calculate_comprehensive_leader_score...")
    test_ts_code = first_stocks[0]
    
    # 检查get_trade_dates
    trade_dates_check = get_trade_dates(25)
    print(f"get_trade_dates(25)返回: {len(trade_dates_check)}个, {trade_dates_check[0]}~{trade_dates_check[-1]}")
    
    # 检查get_stock_history
    df = get_stock_history(test_ts_code, 25)
    print(f"get_stock_history返回: {len(df)}行")
    if not df.empty:
        print(f"日期范围: {df['trade_date'].min()}~{df['trade_date'].max()}")
    
    # 调用calculate_comprehensive_leader_score
    print(f"\n调用calculate_comprehensive_leader_score({test_ts_code})...")
    result = calculate_comprehensive_leader_score(test_ts_code, name_map)
    if result:
        print(f"✅ 返回成功: score={result['total_score']:.1f}")
    else:
        print(f"❌ 返回None")
    
    # 5. 测试identify_theme_leaders
    print(f"\n5. 测试identify_theme_leaders...")
    leaders = identify_theme_leaders(first_stocks, name_map)
    print(f"识别到 {len(leaders)} 个龙头")

if __name__ == "__main__":
    quick_test()
