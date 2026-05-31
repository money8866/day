
# -*- coding: utf-8 -*-
"""
测试 index_component_sw - 申万指数成分股接口
"""
import akshare as ak
import pandas as pd

print("="*80)
print("测试 index_component_sw 接口")
print("="*80)

# 测试 index_component_sw
try:
    print("\n[1] 测试 index_component_sw()")
    # 先看一下函数签名
    import inspect
    print("\n函数签名:")
    sig = inspect.signature(ak.index_component_sw)
    print(f"  {sig}")
    
    # 查看 docstring
    print("\n函数说明:")
    print(ak.index_component_sw.__doc__)
    
    # 尝试调用
    print("\n\n尝试调用 index_component_sw():")
    # 不传参数看看返回什么
    df = ak.index_component_sw()
    print(f"✓ 成功获取数据，共 {len(df)} 条")
    print("\n列名:")
    print(df.columns.tolist())
    print("\n前10行:")
    print(df.head(10))
    df.to_csv("test_index_component_sw.csv", encoding="utf-8-sig", index=False)
    print(f"\n✓ 已保存到 test_index_component_sw.csv")
    
except Exception as e:
    print(f"✗ 调用失败: {e}")
    import traceback
    print(traceback.format_exc())

print("\n[2] 尝试获取申万行业指数的成分股:")
try:
    # 试试传入参数，比如行业名称或者代码
    print("\n尝试传入具体的行业或指数名:")
    # 先试试 '电子'
    print("  - 尝试 '电子':")
    df_electronic = ak.index_component_sw(symbol="电子")
    print(f"  成功获取，共 {len(df_electronic)} 条")
    print(df_electronic.head())
    df_electronic.to_csv("test_electronic.csv", encoding="utf-8-sig", index=False)
    
    print("\n  - 尝试 'AI算力' (如果存在的话):")
    try:
        df_ai = ak.index_component_sw(symbol="AI算力")
        print(f"  成功获取，共 {len(df_ai)} 条")
        df_ai.to_csv("test_ai.csv", encoding="utf-8-sig", index=False)
    except Exception as e:
        print(f"  没有找到 'AI算力' 指数: {e}")

except Exception as e:
    print(f"\n✗ 调用失败: {e}")

print("\n✅ 测试完成！")
