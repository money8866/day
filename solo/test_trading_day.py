# -*- coding: utf-8 -*-
"""
交易日判断功能测试
"""

import sys
sys.path.insert(0, r"d:\mystock\solo")

from limit_track_review import (
    is_trading_day,
    get_previous_trading_day,
    get_smart_trade_date
)
from datetime import datetime

print("="*80)
print("🧪 交易日判断功能测试")
print("="*80)
print()

# 测试1：判断是否为交易日
print("【测试1】判断是否为交易日")
print("-" * 60)

test_dates = [
    ("20260530", "周六"),
    ("20260531", "周日"),
    ("20260528", "周四"),
    ("20260529", "周五"),
]

for date_str, desc in test_dates:
    is_trade = is_trading_day(date_str)
    result = "✅ 交易日" if is_trade else "❌ 非交易日"
    print(f"  {date_str} ({desc}): {result}")

print()

# 测试2：获取上一个交易日
print("【测试2】获取上一个交易日")
print("-" * 60)

for date_str, desc in test_dates:
    prev = get_previous_trading_day(date_str)
    print(f"  {date_str} ({desc}) 的上一个交易日: {prev}")

print()

# 测试3：智能获取交易日期
print("【测试3】智能获取交易日期")
print("-" * 60)

test_cases = [
    ("20260530", "周六，不指定时间"),
    ("20260531", "周日，不指定时间"),
    ("20260528", "周四，交易日"),
]

for date_str, desc in test_cases:
    actual_date, was_specified, msg = get_smart_trade_date(date_str)
    print(f"\n  📅 测试场景: {desc}")
    print(f"     输入日期: {date_str}")
    print(f"     实际日期: {actual_date}")
    if msg:
        print(f"     {msg}")

print()

# 测试4：当前时间判断
print("【测试4】当前时间智能判断")
print("-" * 60)

now = datetime.now()
print(f"  当前时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"  星期: {['一', '二', '三', '四', '五', '六', '日'][now.weekday()]}")

actual_date, was_specified, msg = get_smart_trade_date()
print(f"\n  🎯 系统将使用的日期: {actual_date}")
if msg:
    print(f"  📝 {msg}")

print()
print("="*80)
print("✅ 测试完成")
print("="*80)
