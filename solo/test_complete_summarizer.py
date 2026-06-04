#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
完整测试 daily_analysis_summarizer
"""

import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

print("=" * 70)
print("完整测试 daily_analysis_summarizer")
print("=" * 70)

# 导入我们需要测试的函数
from daily_analysis_summarizer import (
    read_60day_avg_trend_scores, 
    read_market_analysis, 
    read_theme_analysis,
    read_stock_picker,
    generate_summary
)

# 1. 读取60天趋势分
print("\n1. 测试读取60日趋势平均分...")
avg_data = read_60day_avg_trend_scores()
if avg_data:
    print(f"   ✓ 成功读取 {len(avg_data.get('themes', []))} 个主题")
    if avg_data.get('themes'):
        top_theme = avg_data['themes'][0]
        print(f"   TOP1: {top_theme['theme_name']} (平均 {top_theme['avg_trend_score']:.1f} 分)")

# 2. 读取市场分析
print("\n2. 测试读取市场分析...")
market_data = read_market_analysis()
if market_data:
    print(f"   ✓ 成功读取市场分析，包含 {len(market_data.get('indices', []))} 个指数")
    if market_data.get('indices'):
        print(f"   第一个指数: {market_data['indices'][0]['index_name']}")

# 3. 读取主题分析
print("\n3. 测试读取主题分析...")
theme_data = read_theme_analysis()
if theme_data:
    print(f"   ✓ 成功读取主题分析，包含 {len(theme_data.get('themes', []))} 个主题")
    if theme_data.get('themes'):
        top_theme = theme_data['themes'][0]
        print(f"   第一个主题: {top_theme['theme_name']} (趋势 {top_theme.get('trend_score', 0):.1f})")

# 4. 读取股票选股结果
print("\n4. 测试读取个股选股结果...")
stock_data = read_stock_picker()
if stock_data:
    print(f"   ✓ 成功读取 {len(stock_data.get('stocks', []))} 只股票")
    
    stocks = stock_data.get('stocks', [])
    if stocks:
        print(f"\n   股票数据结构（第1只）:")
        print(f"     股票: {stocks[0]['name']} ({stocks[0]['ts_code']})")
        print(f"     主题: {stocks[0]['theme']}")
        print(f"     theme_type: {stocks[0].get('theme_type', 'none')}")
        
        type_counts = {}
        for s in stocks:
            t = s.get('theme_type', 'unknown')
            type_counts[t] = type_counts.get(t, 0) + 1
        print(f"\n   Theme Type 分布:")
        for t, cnt in type_counts.items():
            print(f"     {t}: {cnt} 只")

# 5. 生成完整报告
print("\n5. 测试生成完整报告...")
trade_date = market_data.get('trade_date', '20260602') if market_data else '20260602'

try:
    summary = generate_summary(market_data, theme_data, stock_data, avg_data, trade_date)
    print("   ✓ 成功生成报告")
    print("\n" + "=" * 70)
    print("生成的报告（节选）：")
    print("=" * 70)
    
    # 只显示前3000字符避免太长
    print(summary[:4000])
    
    # 保存完整报告到文件
    output_file = os.path.join(BASE_DIR, 'test_summary_output.txt')
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(summary)
    print(f"\n\n✓ 完整报告已保存到: {output_file}")
    
except Exception as e:
    import traceback
    print(f"   ✗ 生成报告失败: {e}")
    print(traceback.format_exc())

print("\n" + "=" * 70)
print("测试完成！")
print("=" * 70)
