# -*- coding: utf-8 -*-
"""
快速测试脚本 - 验证涨停跟踪系统是否正常工作
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from limit_track_review import (
    get_limit_list_data,
    get_stock_daily_data,
    check_volume_increase,
    CACHE_DIR
)

print("="*60)
print("🧪 涨停跟踪系统快速测试")
print("="*60)

# 1. 测试 Tushare 连接
print("\n[1] 测试 Tushare 连接...")
test_date = "20260529"
limit_df = get_limit_list_data(test_date)

if limit_df is not None and not limit_df.empty:
    print(f"✓ Tushare 连接正常")
    print(f"✓ {test_date} 涨停池数据获取成功，共 {len(limit_df)} 只股票")
    print(f"\n数据列名: {list(limit_df.columns)}")
    print(f"\n前3行数据:")
    print(limit_df.head(3).to_string())
else:
    print("✗ 涨停池数据获取失败")
    sys.exit(1)

# 2. 测试数据缓存目录
print("\n[2] 检查缓存目录...")
if os.path.exists(CACHE_DIR):
    print(f"✓ 缓存目录已存在: {CACHE_DIR}")
else:
    print(f"✓ 缓存目录已创建: {CACHE_DIR}")

# 3. 测试获取日线数据
print("\n[3] 测试获取日线数据...")
test_stock = "000001.SZ"
daily_df = get_stock_daily_data(test_stock, "20260501", "20260529")

if daily_df is not None and not daily_df.empty:
    print(f"✓ 日线数据获取成功，共 {len(daily_df)} 条记录")
    print(f"\n最近3条记录:")
    print(daily_df.tail(3).to_string())
else:
    print("⚠ 日线数据获取失败（可能网络问题）")

# 4. 测试温和放量判断
print("\n[4] 测试温和放量判断逻辑...")
if daily_df is not None and not daily_df.empty:
    is_moderate, vol_ratio = check_volume_increase(daily_df, days=5)
    print(f"温和放量判断: {is_moderate}")
    print(f"量比: {vol_ratio:.2f}")

print("\n" + "="*60)
print("✅ 测试完成！")
print("="*60)
print("\n使用说明:")
print("1. 运行每日涨停跟踪:")
print("   python limit_track_review.py 20260529")
print("\n2. 或者运行自动获取今天的数据:")
print("   python limit_track_review.py")
print("\n3. 报告将保存在:")
print(f"   {os.path.join(CACHE_DIR, 'reviews')}")
print("\n4. 历史记录保存在:")
print(f"   {os.path.join(CACHE_DIR, 'limit_history.json')}")
