
# -*- coding: utf-8 -*-
"""
查找 akshare ETF 成份股接口 - 最后版本
"""
import akshare as ak
import pandas as pd
import inspect

print("="*80)
print("尝试获取 akshare 所有基金和指数相关接口")
print("="*80)

# 1. 看看 akshare 有哪些模块
print("\n[1] akshare 所有可调用的函数:")
try:
    # 列出 akshare 中所有以 'fund_' 或 'index_' 开头的函数
    print("\n以 'fund_' 开头的函数:")
    fund_funcs = [f for f in dir(ak) if f.startswith('fund_')]
    for f in fund_funcs[:30]:  # 先看前30个
        print(f"  - {f}")
    
    print(f"\n... 共 {len(fund_funcs)} 个 fund_ 开头的函数")
    
    print("\n以 'index_' 开头的函数:")
    index_funcs = [f for f in dir(ak) if f.startswith('index_')]
    for f in index_funcs[:30]:
        print(f"  - {f}")
    
    print(f"\n... 共 {len(index_funcs)} 个 index_ 开头的函数")
except Exception as e:
    print(f"✗ 列出函数失败: {e}")

# 2. 试试 fund_etf_fund_info_em 函数
print("\n[2] 测试 fund_etf_fund_info_em 函数，看看参数:")
try:
    # 获取函数签名
    print("  函数签名:")
    sig = inspect.signature(ak.fund_etf_fund_info_em)
    print(f"  {sig}")
    
    # 查看 docstring
    print("\n  函数说明:")
    print(ak.fund_etf_fund_info_em.__doc__[:500])
except Exception as e:
    print(f"✗ 查看失败: {e}")

# 3. 试试 index_zh_a_stock_em 或相关指数成分股接口
print("\n[3] 尝试查找指数成分股接口:")
# 试试一些常见的指数成分股接口名
possible_names = ['index_zh_a_stock_em', 'index_stock_cons', 
                  'index_stock_spot', 'index_stock_zh_a']
for name in possible_names:
    try:
        if hasattr(ak, name):
            print(f"\n✓ 找到 {name}()，尝试调用...")
            df = getattr(ak, name)()
            print(f"  返回 {len(df)} 条数据")
            print(df.head())
            df.to_csv(f"test_{name}.csv", encoding="utf-8-sig", index=False)
            break
    except Exception as e:
        print(f"  {name} 调用失败: {e}")

print("\n✅ 测试完成！")
