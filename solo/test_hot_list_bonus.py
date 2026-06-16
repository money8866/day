#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solo.tushare_quant import count_hot_list_appearances, get_hot_list_bonus

def test_hot_list_bonus():
    """测试热榜出现次数统计功能"""
    print("=" * 80)
    print("测试热榜出现次数统计功能")
    print("=" * 80)
    
    # 测试加分规则
    print("\n[1] 加分规则测试:")
    test_cases = [0, 1, 3, 4, 6, 7, 10, 11, 15, 16, 20]
    for count in test_cases:
        bonus = get_hot_list_bonus(count)
        print(f"  出现{count}次 -> +{bonus}分")
    
    # 测试实际统计
    print("\n[2] 实际统计测试:")
    # 检查缓存目录是否存在
    dc_hot_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'cache_backbone_tushare', 'dc_hot')
    print(f"  热榜缓存目录: {dc_hot_dir}")
    print(f"  目录存在: {os.path.exists(dc_hot_dir)}")
    
    if os.path.exists(dc_hot_dir):
        files = os.listdir(dc_hot_dir)
        csv_files = [f for f in files if f.endswith('.csv')]
        print(f"  缓存文件数量: {len(csv_files)}")
        if csv_files:
            print(f"  最近缓存文件: {csv_files[-1]}")
    
    # 测试统计某只股票
    print("\n[3] 股票热榜出现次数测试:")
    test_stocks = ['000001.SZ', '002594.SZ', '600519.SH']
    for ts_code in test_stocks:
        count = count_hot_list_appearances(ts_code, days=20)
        bonus = get_hot_list_bonus(count)
        print(f"  {ts_code}: 出现{count}次 -> +{bonus}分")
    
    print("\n" + "=" * 80)
    print("测试完成")

if __name__ == "__main__":
    test_hot_list_bonus()