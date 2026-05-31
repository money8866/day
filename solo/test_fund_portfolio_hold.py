
# -*- coding: utf-8 -*-
"""
测试 ak.fund_portfolio_hold_em 接口
"""
import akshare as ak
import pandas as pd

print("="*80)
print("测试 ak.fund_portfolio_hold_em 接口")
print("="*80)

# 1. 先看看函数签名
try:
    import inspect
    sig = inspect.signature(ak.fund_portfolio_hold_em)
    print(f"\n[1] 函数签名: {sig}")
    
    # 查看 docstring
    print("\n[2] 函数说明:")
    print(ak.fund_portfolio_hold_em.__doc__)
except Exception as e:
    print(f"✗ 查看函数信息失败: {e}")

# 2. 尝试调用
print("\n[3] 尝试调用函数:")
try:
    # 先不传参数试试
    print("\n  a. 不传参数:")
    df1 = ak.fund_portfolio_hold_em()
    print(f"✓ 获取到 {len(df1)} 条数据")
    print(f"\n列名: {df1.columns.tolist()}")
    print("\n前10行:")
    print(df1.head(10))
    
    df1.to_csv("test_fund_portfolio_hold_em.csv", encoding="utf-8-sig", index=False)
    print(f"\n✓ 已保存到 test_fund_portfolio_hold_em.csv")
    
    # 尝试传入基金代码，比如 '510300'
    print("\n\n  b. 传入基金代码 '510300' (沪深300ETF):")
    df2 = ak.fund_portfolio_hold_em(symbol="510300")
    print(f"✓ 获取到 {len(df2)} 条数据")
    print(df2.head())
    
    df2.to_csv("test_510300_portfolio.csv", encoding="utf-8-sig", index=False)
    print(f"\n✓ 已保存到 test_510300_portfolio.csv")
    
except Exception as e:
    print(f"✗ 调用失败: {e}")
    import traceback
    print("\n完整错误信息:")
    print(traceback.format_exc())

print("\n✅ 测试完成！")
