#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查主题投资组合的配置"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from theme_rotation_analysis_final import load_theme_portfolio_from_sqlite

print("=" * 80)
print(" 检查主题投资组合的股票配置")
print("=" * 80)

theme_stocks_map, name_map = load_theme_portfolio_from_sqlite()

print(f"\n共 {len(theme_stocks_map)} 个主题\n")
for theme_name, stock_codes in theme_stocks_map.items():
    print(f"主题: {theme_name}")
    print(f"  股票数: {len(stock_codes)}")
    print(f"  股票: {', '.join(stock_codes[:5])}")
    if len(stock_codes) > 5:
        print(f"        ... 和其他 {len(stock_codes)-5} 只")
    print()
