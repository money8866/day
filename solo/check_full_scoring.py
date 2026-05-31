#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""全面检查数据和评分计算
"""
import sys
import os

# 加载环境变量
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 直接从主程序加载必要函数
from theme_rotation_analysis_final import (
    load_theme_portfolio_from_sqlite,
    get_stock_history,
    calculate_comprehensive_leader_score
)

print("=== 加载主题投资组合\n")
theme_stocks_map, name_map = load_theme_portfolio_from_sqlite()
first_theme = list(theme_stocks_map.keys())[0]
first_stocks = list(theme_stocks_map[first_theme])[:5]
print(f"第一个主题：{first_theme}，股票数：{len(theme_stocks_map[first_theme])}")
print(f"前5个股票：{first_stocks}\n")

print("=== 逐个计算前5只股票的评分\n")
for ts_code in first_stocks:
    name = name_map.get(ts_code, "未知")
    print(f"--- {name} ({ts_code})")
    
    # 获取历史数据
    df = get_stock_history(ts_code, 25)
    if not df.empty:
        print(f"  历史数据行数：{len(df)}")
        print(f"  交易日期范围：{df.iloc[-1]['trade_date']} ~ {df.iloc[0]['trade_date']}")
        print(f"  最新日期：{df.iloc[0]['trade_date']}")
        print(f"  最新涨跌幅：{df.iloc[0]['pct_chg']}%")
    
    # 计算评分
    score = calculate_comprehensive_leader_score(ts_code, name_map)
    if score is not None:
        print(f"  评分计算成功！总分：{score['total_score']:.1f}")
    else:
        print("  ❌ 评分计算失败")
    print()
