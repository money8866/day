#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试get_stock_history函数"""

import sys
import os

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入必要的模块
from theme_rotation_analysis_final import (
    load_theme_portfolio_from_sqlite,
    get_stock_history,
    get_trade_dates,
    cached_daily
)

def test_get_stock_history():
    """测试get_stock_history函数"""
    print("="*60)
    print("测试get_stock_history函数")
    print("="*60)
    
    # 1. 检查trade_dates
    print("\n1. 检查trade_dates...")
    trade_dates = get_trade_dates(25)
    print(f"trade_dates数量: {len(trade_dates)}")
    print(f"trade_dates: {trade_dates}")
    
    # 2. 测试cached_daily
    print("\n2. 测试cached_daily...")
    test_ts_code = '688082.SH'
    start_date = trade_dates[0]
    end_date = trade_dates[-1]
    print(f"测试股票: {test_ts_code}")
    print(f"start_date: {start_date}, end_date: {end_date}")
    
    df_cached = cached_daily(test_ts_code, start_date, end_date)
    print(f"cached_daily返回的数据行数: {len(df_cached)}")
    
    if not df_cached.empty:
        print(f"cached_daily的日期范围: {df_cached['trade_date'].min()} ~ {df_cached['trade_date'].max()}")
        print(f"cached_daily最新日期: {df_cached.iloc[-1]['trade_date']}")
    
    # 3. 测试get_stock_history
    print("\n3. 测试get_stock_history...")
    df_history = get_stock_history(test_ts_code, 25)
    print(f"get_stock_history返回的数据行数: {len(df_history)}")
    
    if not df_history.empty:
        print(f"get_stock_history的日期范围: {df_history['trade_date'].min()} ~ {df_history['trade_date'].max()}")
        print(f"get_stock_history最新日期: {df_history.iloc[-1]['trade_date']}")
    else:
        print(f"❌ get_stock_history返回空数据！")
    
    # 4. 检查过滤逻辑
    print("\n4. 检查过滤逻辑...")
    df_cached['trade_date'] = df_cached['trade_date'].astype(str)
    df_filtered = df_cached[df_cached['trade_date'].isin(trade_dates)]
    print(f"过滤后的数据行数: {len(df_filtered)}")
    
    # 5. 模拟calculate_theme_historical_rankings中的调用
    print("\n5. 模拟calculate_theme_historical_rankings中的调用...")
    date = trade_dates[-1]
    date_idx = len(trade_dates) - 1
    print(f"date: {date}, date_idx: {date_idx}")
    
    df_history_for_calc = get_stock_history(test_ts_code, date_idx + 5)
    print(f"获取到的数据行数: {len(df_history_for_calc)}")
    
    if not df_history_for_calc.empty and date in df_history_for_calc['trade_date'].values:
        print(f"✅ {date}在历史数据中")
        daily_data = df_history_for_calc[df_history_for_calc['trade_date'] == date].iloc[0]
        print(f"当日涨跌幅: {daily_data['pct_chg']}%")
    else:
        print(f"❌ {date}不在历史数据中")
        print(f"历史数据日期: {df_history_for_calc['trade_date'].tolist()}")
        print(f"trade_dates最后5个: {trade_dates[-5:]}")

if __name__ == "__main__":
    test_get_stock_history()
