"""
BullScore v3.1 优化验证脚本
测试北摩高科(002985)评分是否合理
"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')
sys.path.insert(0, r'D:\mystock')

import pandas as pd
from pathlib import Path
import json

def test_beimo():
    """测试北摩高科评分"""
    # 直接从已有的CSV读取结果
    csv_path = Path(r'D:\mystock\solo\report_daily\bull_stocks_qualified.csv')
    if not csv_path.exists():
        print(f"CSV文件不存在: {csv_path}")
        return
        
    df = pd.read_csv(csv_path, dtype={'code': str})
    row = df[df['code'].str.strip() == '002985']
    
    if len(row) == 0:
        print("未找到北摩高科(002985)数据")
        print(f"现有股票: {df['code'].head(10).tolist()}")
        return
    
    r = row.iloc[0]
    print(f"=== 北摩高科(002985) BullScore 评分详情 ===\n")
    print(f"股票: {r['name']} ({r['code']})")
    print(f"行业: {r['industry']}")
    print(f"主题: {r['theme']}")
    print(f"\n--- 评分详情 ---")
    print(f"产业景气: {r['产业景气']:.2f}")
    print(f"技术壁垒: {r['技术壁垒']:.2f}")
    print(f"订单爆发: {r['订单爆发']:.2f}")
    print(f"业绩质量: {r['业绩质量']:.2f}")
    print(f"龙头地位: {r['龙头地位']:.2f}")
    print(f"预期差: {r['预期差']:.2f}")
    print(f"机构认可: {r['机构认可']:.2f}")
    print(f"市值弹性: {r['市值弹性']:.2f}")
    print(f"筹码面: {r['筹码面']:.2f}")
    print(f"估值安全: {r['估值安全']:.2f}")
    print(f"\n--- 原始数据 ---")
    print(f"营收同比: {r['营收同比']:.2f}%")
    print(f"利润同比: {r['利润同比']:.2f}%")
    print(f"ROE: {r['ROE']:.2f}%")
    print(f"毛利率: {r['毛利率']:.2f}%")
    print(f"研发投入%: {r['研发投入%']:.2f}%")
    print(f"市值(亿): {r['市值(亿)']:.2f}")
    print(f"\n--- 最终评分 ---")
    print(f"Bull_v2.1分: {r['Bull_v2.1分']:.2f}")
    print(f"主题分v2: {r['主题分v2']:.2f}")
    print(f"最终分: {r['最终分']:.2f}")
    print(f"等级: {r['等级']}")

if __name__ == '__main__':
    test_beimo()
