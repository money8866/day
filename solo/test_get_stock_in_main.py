#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试get_stock_history在主程序中的行为"""

import sys
import os

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入必要的模块
from theme_rotation_analysis_final import (
    load_theme_portfolio_from_sqlite,
    calculate_theme_historical_rankings,
    get_stock_history,
    get_trade_dates,
    cached_daily
)

def test_get_stock_in_main():
    """测试get_stock_history在主程序中的行为"""
    print("="*60)
    print("测试get_stock_history在主程序中的行为")
    print("="*60)
    
    # 1. 模拟主程序的调用
    print("\n1. 模拟主程序的调用...")
    trade_dates = ['20260529']
    
    # 2. 模拟calculate_theme_historical_rankings的调用
    print("\n2. 模拟calculate_theme_historical_rankings的调用...")
    start_idx = max(0, len(trade_dates) - 20)
    print(f"start_idx: {start_idx}")
    
    for date_idx in range(start_idx, len(trade_dates)):
        date = trade_dates[date_idx]
        print(f"\n处理日期: {date}, date_idx: {date_idx}")
        
        # 测试get_stock_history
        test_ts_code = '688082.SH'
        n_days = date_idx + 5
        print(f"  调用 get_stock_history({test_ts_code}, {n_days})")
        
        df = get_stock_history(test_ts_code, n_days)
        print(f"  返回的数据行数: {len(df)}")
        
        if not df.empty:
            print(f"  数据日期范围: {df['trade_date'].min()} ~ {df['trade_date'].max()}")
            
            # 检查date是否在df中
            if date in df['trade_date'].values:
                print(f"  ✅ {date}在数据中")
            else:
                print(f"  ❌ {date}不在数据中")
                print(f"  df的日期: {df['trade_date'].tolist()}")
        else:
            print(f"  ⚠️ 返回空数据！")
            
            # 检查cached_daily
            trade_dates_for_cached = get_trade_dates(n_days)
            start_date = trade_dates_for_cached[0]
            end_date = trade_dates_for_cached[-1]
            print(f"  trade_dates: {trade_dates_for_cached}")
            
            df_cached = cached_daily(test_ts_code, start_date, end_date)
            print(f"  cached_daily返回的数据行数: {len(df_cached)}")
            
            if not df_cached.empty:
                print(f"  cached_daily的日期: {df_cached['trade_date'].astype(str).tolist()[:10]}")
                
                # 过滤
                df_cached['trade_date'] = df_cached['trade_date'].astype(str)
                df_filtered = df_cached[df_cached['trade_date'].isin(trade_dates_for_cached)]
                print(f"  过滤后的数据行数: {len(df_filtered)}")
                
                if not df_filtered.empty:
                    print(f"  过滤后的日期: {df_filtered['trade_date'].tolist()}")
                else:
                    print(f"  ⚠️ 过滤后数据为空！")
                    print(f"  原因：cached_daily的日期不在trade_dates中")
                    print(f"  trade_dates: {trade_dates_for_cached}")
                    print(f"  cached_daily的日期（转换为str）: {df_cached['trade_date'].astype(str).tolist()}")

if __name__ == "__main__":
    test_get_stock_in_main()
