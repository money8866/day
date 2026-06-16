#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solo.theme_trend_sentiment_score import (
    get_dc_hot, get_dc_hot_multi_days, TRADE_DATE,
    DC_HOT_CACHE_DIR, get_stock_hot_rank, load_dc_hot
)

def verify_hot_data():
    """验证热榜数据获取和应用"""
    print("=" * 80)
    print("热榜数据验证报告")
    print("=" * 80)
    
    # 1. 获取多日热榜数据
    print("\n[1] 尝试获取最近2天的热榜数据...")
    multi_data = get_dc_hot_multi_days(days=2)
    
    if not multi_data:
        print("警告：未能获取热榜数据")
        return
    
    # 2. 显示获取到的热榜数据
    for date, df in multi_data.items():
        print(f"\n日期: {date}, 热榜个股数: {len(df)}")
        if len(df) > 0:
            print("前10名热榜个股:")
            top10 = df.sort_values('hot_rank').head(10)
            for _, row in top10.iterrows():
                score = get_stock_hot_rank(row['ts_code'])
                print(f"  排名{row['hot_rank']:3d}: {row['ts_code']} {row['ts_name']} - 热度分: +{score}")
    
    # 3. 显示缓存目录
    print(f"\n[2] 热榜缓存目录: {DC_HOT_CACHE_DIR}")
    if os.path.exists(DC_HOT_CACHE_DIR):
        cache_files = [f for f in os.listdir(DC_HOT_CACHE_DIR) if f.startswith('dc_hot_')]
        if cache_files:
            print(f"已缓存的热榜文件: {', '.join(cache_files)}")
        else:
            print("暂无缓存文件")
    else:
        print("缓存目录不存在")
    
    # 4. 测试热度分计算
    print("\n[3] 热度分计算规则:")
    test_ranks = [1, 10, 11, 30, 31, 50, 51, 70, 71, 100, 101]
    for rank in test_ranks:
        # 创建模拟数据
        import pandas as pd
        test_df = pd.DataFrame({
            'ts_code': ['600000.SH'],
            'hot_rank': [rank],
            'ts_name': ['测试股票']
        })
        
        # 临时替换全局变量测试
        from solo.theme_trend_sentiment_score import _dc_hot_df, _dc_hot_date
        original_df, original_date = _dc_hot_df, _dc_hot_date
        
        try:
            globals()['_dc_hot_df'] = test_df
            globals()['_dc_hot_date'] = TRADE_DATE
            score = get_stock_hot_rank('600000.SH')
            print(f"  排名{rank:3d}: +{score}分")
        finally:
            globals()['_dc_hot_df'], globals()['_dc_hot_date'] = original_df, original_date
    
    print("\n" + "=" * 80)
    print("验证完成")

if __name__ == "__main__":
    verify_hot_data()