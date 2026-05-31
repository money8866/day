
# -*- coding: utf-8 -*-
"""
进一步查找 akshare ETF 成份股接口
"""
import akshare as ak
import pandas as pd

print("="*80)
print("继续查找 ETF 成份股接口")
print("="*80)

# 1. 试试 fund_etf_stock_em 或相关接口
print("\n[1] 查找 ETF 成份股相关接口")
try:
    # 试试 fund_etf_stock_em
    print("  测试 fund_etf_stock_em()")
    # 试试传入 '510300'
    stock_em = ak.fund_etf_stock_em()
    print(f"✓ 数据获取成功")
    print(stock_em.head())
except Exception as e:
    print(f"✗ 调用失败: {e}")

# 2. 试试 index 模块的成份股接口
print("\n[2] 试试 index 模块")
try:
    # 试试 index_stock_cons_em - 东方财富成分股
    print("  测试 index_stock_cons_em()")
    # 试试传入 '000300' 或 'sh000300'
    index_cons = ak.index_stock_cons_em(symbol="000300")
    print(f"✓ 成份股获取成功，共 {len(index_cons)} 只")
    print(index_cons.head(10))
    index_cons.to_csv("test_index_cons_em.csv", encoding="utf-8-sig", index=False)
    print(f"\n✓ 已保存为 test_index_cons_em.csv")
except Exception as e:
    print(f"✗ 调用失败: {e}")

# 3. 试试获取所有指数列表
print("\n[3] 试试 index_member_ths - 同花顺指数成员")
try:
    print("  测试 index_member_ths()")
    index_member = ak.index_member_ths()
    print(f"✓ 指数成员获取成功")
    print(index_member.head())
except Exception as e:
    print(f"✗ 调用失败: {e}")

print("\n✅ 测试完成！")
