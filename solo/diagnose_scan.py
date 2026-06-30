"""
诊断：检查主循环为什么0个信号
"""
import sys
import os

sys.path.insert(0, r'D:\mystock\solo')

from trend_entry_precision import load_qualified_pool, get_data

# 1. 检查合格股池
print('=' * 60)
print('1. 检查合格股池')
print('=' * 60)
codes = load_qualified_pool()
print(f'合格股池数量: {len(codes)}')
print(f'前5只: {codes[:5]}')
print()

# 2. 检查前10只股票是否有数据
print('=' * 60)
print('2. 检查前10只股票数据')
print('=' * 60)
valid_count = 0
for i, code in enumerate(codes[:10]):
    df = get_data(code)
    if df is not None and len(df) >= 60:
        valid_count += 1
        print(f'{i+1}. {code}: ✅ {len(df)}天数据')
    elif df is not None and len(df) < 60:
        print(f'{i+1}. {code}: ⚠️ 仅{len(df)}天数据')
    else:
        print(f'{i+1}. {code}: ❌ 无数据')
print()
print(f'有效数据 (≥60天): {valid_count}/10')
print()

# 3. 检查600460.SH（已知有信号）
print('=' * 60)
print('3. 检查600460.SH（已知有信号）')
print('=' * 60)
df = get_data('600460.SH')
if df is not None:
    print(f'数据天数: {len(df)}')
    print(f'日期范围: {df["trade_date"].min()} 至 {df["trade_date"].max()}')
    print(f'最新日期: {df["trade_date"].iloc[-1]}')
    print(f'是否有20260616数据: {("20260616" in df["trade_date"].values)}')
print()

print('=' * 60)
print('结论：如果前10只股票都无有效数据，说明get_data()有问题')
print('=' * 60)
