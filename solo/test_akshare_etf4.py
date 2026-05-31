
# -*- coding: utf-8 -*-
"""
直接测试 fund_etf_fund_daily_em 接口，不带参数
"""
import akshare as ak
import pandas as pd

print("="*80)
print("直接测试 fund_etf_fund_daily_em 接口")
print("="*80)

# 直接调用，不带任何参数
try:
    print("\n尝试调用 fund_etf_fund_daily_em()")
    df = ak.fund_etf_fund_daily_em()
    print(f"✓ 成功获取数据，共 {len(df)} 条")
    print("\n列名:")
    print(df.columns.tolist())
    print("\n前10行数据:")
    print(df.head(10))
    
    df.to_csv("test_fund_etf_daily_em.csv", encoding="utf-8-sig", index=False)
    print(f"\n✓ 已保存为 test_fund_etf_daily_em.csv")
except Exception as e:
    print(f"✗ 调用失败: {e}")
    import traceback
    print("\n完整错误信息:")
    print(traceback.format_exc())

print("\n✅ 测试完成！")
