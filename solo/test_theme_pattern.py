#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solo.tushare_quant import load_theme_pattern_stocks

def test_load_theme_pattern_stocks():
    """测试主题选股结果加载"""
    print("=" * 80)
    print("测试 load_theme_pattern_stocks() 函数")
    print("=" * 80)
    
    records, text_output = load_theme_pattern_stocks()
    
    if not records:
        print("警告：未能加载主题选股结果")
        print("请确保 theme_pattern_stocks.csv 文件存在")
        return
    
    print(f"\n[1] 加载到 {len(records)} 条记录")
    
    # 统计各状态分布
    state_counts = {}
    buy_type_counts = {}
    
    for record in records:
        state = record.get('theme_state', '未知')
        buy_type = record.get('buy_type', '未知')
        
        state_counts[state] = state_counts.get(state, 0) + 1
        buy_type_counts[buy_type] = buy_type_counts.get(buy_type, 0) + 1
    
    print("\n[2] 主题状态分布:")
    for state, count in state_counts.items():
        print(f"  {state}: {count}只")
    
    print("\n[3] 买入类型分布:")
    for buy_type, count in buy_type_counts.items():
        print(f"  {buy_type}: {count}只")
    
    # 测试中期/短期分组
    print("\n[4] 输出报告预览:")
    print("-" * 60)
    lines = text_output.split('\n')
    for i, line in enumerate(lines[:30]):
        print(line)
    
    print("\n" + "=" * 80)
    print("测试完成")

if __name__ == "__main__":
    test_load_theme_pattern_stocks()