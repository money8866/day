#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试calculate_comprehensive_leader_score函数"""

import sys
import os

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入必要的模块
from theme_rotation_analysis_final import (
    load_theme_portfolio_from_sqlite,
    calculate_comprehensive_leader_score
)

def test_calculate_comprehensive():
    """测试calculate_comprehensive_leader_score函数"""
    print("="*60)
    print("测试calculate_comprehensive_leader_score函数")
    print("="*60)
    
    # 1. 加载主题投资组合
    print("\n1. 加载主题投资组合...")
    theme_stocks_map, name_map = load_theme_portfolio_from_sqlite()
    first_theme = list(theme_stocks_map.keys())[0]
    first_stocks = theme_stocks_map[first_theme]
    print(f"测试主题: {first_theme}")
    print(f"测试股票: {first_stocks[:5]}")
    
    # 2. 测试每只股票的评分
    print("\n2. 测试每只股票的评分...")
    results = []
    for i, ts_code in enumerate(first_stocks[:10]):
        try:
            result = calculate_comprehensive_leader_score(ts_code, name_map)
            if result:
                results.append((ts_code, result['total_score']))
                print(f"  {i+1}. {ts_code}: ✅ score={result['total_score']:.1f}")
            else:
                print(f"  {i+1}. {ts_code}: ❌ 返回None")
        except Exception as e:
            print(f"  {i+1}. {ts_code}: ❌ 异常 - {e}")
            import traceback
            traceback.print_exc()
    
    # 3. 检查identify_theme_leaders函数
    print("\n3. 测试identify_theme_leaders函数...")
    from theme_rotation_analysis_final import identify_theme_leaders
    leaders = identify_theme_leaders(first_stocks, name_map)
    print(f"识别到的龙头数量: {len(leaders)}")
    
    if leaders:
        print(f"龙头详情:")
        for i, leader in enumerate(leaders[:5]):
            print(f"  {i+1}. {leader['name']} ({leader['ts_code']}): score={leader['total_score']:.1f}")
    else:
        print(f"⚠️ 返回0个龙头！")
        print(f"让我检查一下calculate_comprehensive_leader_score是否正常...")
        
        # 逐个测试
        print(f"\n逐个测试每只股票:")
        for i, ts_code in enumerate(first_stocks[:10]):
            try:
                result = calculate_comprehensive_leader_score(ts_code, name_map)
                print(f"  {i+1}. {ts_code}: {type(result)}, {result is not None}")
            except Exception as e:
                print(f"  {i+1}. {ts_code}: 异常 - {e}")

if __name__ == "__main__":
    test_calculate_comprehensive()
