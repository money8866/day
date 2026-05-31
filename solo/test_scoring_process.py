#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试评分计算过程"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from theme_rotation_analysis_final import load_theme_portfolio_from_sqlite, calculate_comprehensive_leader_score

print("=== 加载主题投资组合 ===\n")
theme_stocks_map, name_map = load_theme_portfolio_from_sqlite()

print("=== 测试计算 688082.SH 的评分 ===\n")
result = calculate_comprehensive_leader_score('688082.SH', name_map)

if result:
    print(f"评分计算成功！总分: {result['total_score']:.1f}")
    print(f"5日涨幅: {result['change_5']:.1f}%")
    print(f"20日涨幅: {result['change_20']:.1f}%")
    print(f"详细信息: {result}")
else:
    print("评分计算失败！")

print("\n=== 测试计算 600021.SH 的评分 ===\n")
result2 = calculate_comprehensive_leader_score('600021.SH', name_map)
if result2:
    print(f"评分计算成功！总分: {result2['total_score']:.1f}")
    print(f"5日涨幅: {result2['change_5']:.1f}%")
    print(f"20日涨幅: {result2['change_20']:.1f}%")
else:
    print("评分计算失败！")
