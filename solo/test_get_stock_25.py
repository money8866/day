#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试get_stock_history(ts_code, 25)的调用"""

import sys
import os

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入必要的模块
from theme_rotation_analysis_final import (
    get_stock_history,
    get_trade_dates,
    cached_daily
)

def test_get_stock_25():
    """测试get_stock_history(ts_code, 25)"""
    print("="*60)
    print("测试get_stock_history(ts_code, 25)")
    print("="*60)
    
    test_ts_code = '688082.SH'
    
    # 1. 直接调用get_stock_history(ts_code, 25)
    print(f"\n1. 调用 get_stock_history({test_ts_code}, 25)")
    df = get_stock_history(test_ts_code, 25)
    print(f"   返回的数据行数: {len(df)}")
    
    if not df.empty:
        print(f"   日期范围: {df['trade_date'].min()} ~ {df['trade_date'].max()}")
    else:
        print(f"   ⚠️ 返回空数据！")
    
    # 2. 检查get_trade_dates(25)
    print(f"\n2. 检查 get_trade_dates(25)")
    trade_dates = get_trade_dates(25)
    print(f"   返回的交易日数量: {len(trade_dates)}")
    print(f"   日期范围: {trade_dates[0]} ~ {trade_dates[-1]}")
    print(f"   最近5个: {trade_dates[-5:]}")
    
    # 3. 检查cached_daily
    print(f"\n3. 检查 cached_daily")
    start_date = trade_dates[0]
    end_date = trade_dates[-1]
    df_cached = cached_daily(test_ts_code, start_date, end_date)
    print(f"   返回的数据行数: {len(df_cached)}")
    
    if not df_cached.empty:
        print(f"   日期范围: {df_cached['trade_date'].min()} ~ {df_cached['trade_date'].max()}")
        print(f"   最近5个日期: {df_cached['trade_date'].astype(str).tolist()[-5:]}")
    
    # 4. 检查过滤后的数据
    print(f"\n4. 检查过滤后的数据")
    if not df_cached.empty:
        df_cached['trade_date'] = df_cached['trade_date'].astype(str)
        df_filtered = df_cached[df_cached['trade_date'].isin(trade_dates)]
        print(f"   过滤后的数据行数: {len(df_filtered)}")
        
        if not df_filtered.empty:
            print(f"   过滤后的日期: {df_filtered['trade_date'].tolist()}")

if __name__ == "__main__":
    test_get_stock_25()
