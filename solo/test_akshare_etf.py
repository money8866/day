
# -*- coding: utf-8 -*-
"""
测试 akshare 获取 ETF 成份股
"""
import akshare as ak
import pandas as pd

print("="*80)
print("测试 akshare ETF 接口")
print("="*80)

# 1. 先测试 fund_etf_fund_daily_em 接口
print("\n[1] 测试 fund_etf_fund_daily_em()")
try:
    # 传入 000001 作为测试（这个是上证指数ETF，或者随便传个ETF代码）
    etf_df = ak.fund_etf_fund_daily_em(symbol="510300")
    print(f"✓ 数据获取成功，共 {len(etf_df)} 条")
    print("\n前5行数据:")
    print(etf_df.head())
    
    # 保存为 CSV 查看
    etf_df.to_csv("test_etf_fund_daily.csv", encoding="utf-8-sig", index=False)
    print(f"\n✓ 已保存到 test_etf_fund_daily.csv")
except Exception as e:
    print(f"✗ 调用失败: {e}")

# 2. 再试试 fund_etf_hist_sina 或者其他 ETF 相关接口
print("\n[2] 测试 fund_etf_hist_sina()")
try:
    # 获取 510300 的历史数据
    hist_df = ak.fund_etf_hist_sina(symbol="sz510300")
    print(f"✓ 数据获取成功，共 {len(hist_df)} 条")
    print("\n前5行数据:")
    print(hist_df.head())
except Exception as e:
    print(f"✗ 调用失败: {e}")

# 3. 看看有没有直接获取成份股的接口
print("\n[3] 查找 ETF 成份股相关接口")
# 搜索一下 akshare 文档，看看有没有专门获取成份股的
try:
    # 试试 fund_etf_fund_info_em
    print("\n 测试 fund_etf_fund_info_em()")
    info_df = ak.fund_etf_fund_info_em(symbol="510300")
    print(f"✓ 数据获取成功")
    print(info_df)
except Exception as e:
    print(f"✗ 调用失败: {e}")

print("\n✅ 测试完成！")
