
# -*- coding: utf-8 -*-
"""
查找 akshare ETF 成份股相关接口
"""
import akshare as ak
import pandas as pd

print("="*80)
print("探索 akshare 接口，查找 ETF 成份股")
print("="*80)

# 1. 先看看指数成份股接口
print("\n[1] 测试指数成份股接口")
try:
    # 沪深300成份股
    print("  测试 index_stock_cons_sina()")
    index_df = ak.index_stock_cons_sina(symbol="sh000300")
    print(f"✓ 沪深300成份股，共 {len(index_df)} 只")
    print(index_df.head())
    
    index_df.to_csv("test_index_cons.csv", encoding="utf-8-sig", index=False)
    print(f"\n✓ 已保存到 test_index_cons.csv")
except Exception as e:
    print(f"✗ 调用失败: {e}")

# 2. 试试 ETF 实时行情
print("\n[2] 测试 ETF 实时行情")
try:
    etf_qt = ak.fund_etf_spot_quote_em()
    print(f"✓ ETF 实时行情，共 {len(etf_qt)} 只")
    print(etf_qt[['代码', '名称', '最新价']].head(10))
    etf_qt.to_csv("test_etf_quote.csv", encoding="utf-8-sig", index=False)
except Exception as e:
    print(f"✗ 调用失败: {e}")

# 3. 看看 fund 模块有哪些接口
print("\n[3] 查找 fund 相关接口")
try:
    # 先获取所有ETF列表
    print("  测试 fund_etf_fund_info_em")
    fund_info = ak.fund_etf_fund_info_em()
    print(f"✓ 基金信息表，共 {len(fund_info)} 条")
    print(fund_info.head(10))
    fund_info.to_csv("test_etf_list.csv", encoding="utf-8-sig", index=False)
except Exception as e:
    print(f"✗ 调用失败: {e}")

print("\n✅ 测试完成！")
