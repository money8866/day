#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""详细调试主题评分问题"""

import sys
import os
import traceback

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入必要的模块
from theme_rotation_analysis_final import (
    load_theme_portfolio_from_sqlite,
    identify_theme_leaders,
    calculate_comprehensive_leader_score
)

def debug_scoring():
    """详细调试评分问题"""
    print("="*60)
    print("详细调试主题评分问题")
    print("="*60)
    
    # 1. 加载主题投资组合
    print("\n1. 加载主题投资组合...")
    theme_stocks_map, name_map = load_theme_portfolio_from_sqlite()
    print(f"加载了 {len(theme_stocks_map)} 个主题，{len(name_map)} 只股票")
    
    # 2. 调试identify_theme_leaders函数
    print("\n2. 调试identify_theme_leaders函数...")
    first_theme = list(theme_stocks_map.keys())[0]
    first_stocks = theme_stocks_map[first_theme]
    print(f"主题: {first_theme}")
    print(f"股票列表: {first_stocks}")
    
    print(f"\n开始调用identify_theme_leaders...")
    try:
        leaders = identify_theme_leaders(first_stocks, name_map)
        print(f"✅ identify_theme_leaders返回成功")
        print(f"返回的龙头数量: {len(leaders)}")
        
        if leaders:
            print(f"\n龙头详情:")
            for i, leader in enumerate(leaders[:5]):
                print(f"  {i+1}. {leader['name']} ({leader['ts_code']}): total_score={leader['total_score']:.1f}")
        else:
            print(f"\n⚠️ 返回的leaders列表为空")
            
    except Exception as e:
        print(f"❌ identify_theme_leaders执行失败: {e}")
        traceback.print_exc()
    
    # 3. 测试calculate_comprehensive_leader_score对每只股票
    print(f"\n3. 测试calculate_comprehensive_leader_score对每只股票...")
    for i, ts_code in enumerate(first_stocks[:10]):
        try:
            result = calculate_comprehensive_leader_score(ts_code, name_map)
            if result:
                print(f"  {ts_code}: ✅ total_score={result['total_score']:.1f}")
            else:
                print(f"  {ts_code}: ❌ 返回None")
        except Exception as e:
            print(f"  {ts_code}: ❌ 异常 - {e}")
            traceback.print_exc()

if __name__ == "__main__":
    debug_scoring()
