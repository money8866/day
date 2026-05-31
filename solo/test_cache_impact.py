#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试calculate_theme_historical_rankings对缓存的影响"""

import sys
import os

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入必要的模块
from theme_rotation_analysis_final import (
    load_theme_portfolio_from_sqlite,
    calculate_theme_historical_rankings,
    identify_theme_leaders,
    calculate_comprehensive_leader_score,
    get_stock_history
)

def test_cache_impact():
    """测试calculate_theme_historical_rankings对缓存的影响"""
    print("="*60)
    print("测试calculate_theme_historical_rankings对缓存的影响")
    print("="*60)
    
    # 1. 加载主题投资组合
    print("\n1. 加载主题投资组合...")
    theme_stocks_map, name_map = load_theme_portfolio_from_sqlite()
    first_theme = list(theme_stocks_map.keys())[0]
    first_stocks = theme_stocks_map[first_theme]
    print(f"测试主题: {first_theme}")
    
    # 2. 调用calculate_theme_historical_rankings之前测试
    print("\n2. 调用calculate_theme_historical_rankings之前...")
    trade_dates = ['20260529']
    test_ts_code = first_stocks[0]
    
    print(f"  测试股票: {test_ts_code}")
    df_before = get_stock_history(test_ts_code, 25)
    print(f"  get_stock_history数据行数: {len(df_before)}")
    if not df_before.empty:
        print(f"  最新日期: {df_before.iloc[-1]['trade_date']}")
    
    result_before = calculate_comprehensive_leader_score(test_ts_code, name_map)
    if result_before:
        print(f"  calculate_comprehensive_leader_score: ✅ score={result_before['total_score']:.1f}")
    else:
        print(f"  calculate_comprehensive_leader_score: ❌ 返回None")
    
    # 3. 调用calculate_theme_historical_rankings
    print("\n3. 调用calculate_theme_historical_rankings...")
    print("  开始计算...")
    theme_summary = calculate_theme_historical_rankings(theme_stocks_map, trade_dates)
    print(f"  计算完成，共 {len(theme_summary)} 个主题")
    
    # 4. 调用calculate_theme_historical_rankings之后测试
    print("\n4. 调用calculate_theme_historical_rankings之后...")
    
    print(f"  测试股票: {test_ts_code}")
    df_after = get_stock_history(test_ts_code, 25)
    print(f"  get_stock_history数据行数: {len(df_after)}")
    if not df_after.empty:
        print(f"  最新日期: {df_after.iloc[-1]['trade_date']}")
    else:
        print(f"  ⚠️ get_stock_history返回空数据！")
    
    result_after = calculate_comprehensive_leader_score(test_ts_code, name_map)
    if result_after:
        print(f"  calculate_comprehensive_leader_score: ✅ score={result_after['total_score']:.1f}")
    else:
        print(f"  ⚠️ calculate_comprehensive_leader_score: ❌ 返回None")
    
    # 5. 测试identify_theme_leaders
    print("\n5. 测试identify_theme_leaders...")
    leaders = identify_theme_leaders(first_stocks, name_map)
    print(f"识别到的龙头数量: {len(leaders)}")
    
    if leaders:
        print(f"龙头详情:")
        for i, leader in enumerate(leaders[:5]):
            print(f"  {i+1}. {leader['name']} ({leader['ts_code']}): score={leader['total_score']:.1f}")
    else:
        print(f"⚠️ 返回0个龙头！")

if __name__ == "__main__":
    test_cache_impact()
