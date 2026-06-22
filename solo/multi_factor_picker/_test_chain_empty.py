# -*- coding: utf-8 -*-
"""测试实际选股流程中的产业链识别效果"""
import sys, os
sys.path.insert(0, '.')
from main import run_picker
from chain_mapping import identify_stock_chain_v3, identify_stock_chain_v2, load_theme_json

# 运行选股
print("运行选股流程...")
result_df = run_picker()

# 统计产业链为空的情况
total = len(result_df)
empty_chain = len(result_df[result_df['chain'].isna() | (result_df['chain'] == '')])
print(f"\n总股票数: {total}")
print(f"产业链为空: {empty_chain} ({empty_chain/total*100:.1f}%)")

# 显示产业链分布
print("\n产业链分布:")
chain_counts = result_df['chain'].value_counts()
for chain, count in chain_counts.items():
    print(f"  {chain}: {count}")

# 显示产业链为空的股票
if empty_chain > 0:
    print("\n产业链为空的股票:")
    empty_stocks = result_df[result_df['chain'].isna() | (result_df['chain'] == '')]
    for _, row in empty_stocks.head(20).iterrows():
        print(f"  {row['stock_name']} ({row['industry']})")
