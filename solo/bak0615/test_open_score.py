"""
回溯测试：300706 阿石创 20260610 开仓分评分
"""
import sys
import os
sys.path.insert(0, r'd:\mystock\solo')

# 强制覆盖 expanduser
os.path.expanduser = lambda x: r'd:\mystock'

import pandas as pd
import numpy as np

# 直接导入，不依赖其他模块
exec(open(r'd:\mystock\solo\tushare_quant.py', encoding='utf-8').read().split('def calc_dual_layer_score_v75')[0])

# 读取核心函数
code = open(r'd:\mystock\solo\tushare_quant.py', encoding='utf-8').read()

# 提取需要的函数
import re
funcs_to_extract = [
    'def barslast',
    'def calc_position_factor',
    'def calc_structure_type',
    'def calc_trend_score',
    'def calc_money_score',
    'def calc_dual_layer_score_v75',
    'def calc_hot_money_open_score_v9',
    'def get_stock_name',
]

# 简化测试：直接模拟数据
def test_stock_open_score():
    """回溯测试股票开仓分"""
    ts_code = "300706.SZ"
    trade_date = "20260610"
    
    print(f"\n{'='*60}")
    print(f"回溯测试：{ts_code} @ {trade_date}")
    print('='*60)
    
    # 生成模拟日线数据（基于阿石创的历史特征）
    # 阿石创是科创板股票，主营光学材料
    dates = pd.date_range(end='2026-06-10', periods=250, freq='B')
    np.random.seed(300706)
    
    base_price = 28.0
    prices = [base_price]
    for i in range(249):
        change = np.random.normal(0.001, 0.03)
        new_price = prices[-1] * (1 + change)
        prices.append(new_price)
    
    # 模拟近期走势
    prices[-5:] = [prices[-6]*1.05, prices[-5]*1.03, prices[-4]*0.98, prices[-3]*1.02, prices[-2]*1.06]
    
    df = pd.DataFrame({
        'trade_date': [d.strftime('%Y%m%d') for d in dates],
        'open': [p * np.random.uniform(0.99, 1.01) for p in prices],
        'high': [p * np.random.uniform(1.00, 1.04) for p in prices],
        'low': [p * np.random.uniform(0.96, 1.00) for p in prices],
        'close': prices,
        'vol': [np.random.uniform(1000000, 5000000) for _ in range(250)],
        'pct_chg': [0.0] + [((prices[i] - prices[i-1]) / prices[i-1]) * 100 for i in range(1, 250)]
    })
    
    print(f"数据范围: {df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]}")
    print(f"最近5天: ")
    for i in range(-5, 0):
        print(f"  {df.iloc[i]['trade_date']}: 收盘{df.iloc[i]['close']:.2f} 涨跌幅{df.iloc[i]['pct_chg']:.2f}%")
    
    # 模拟股票信息
    stock_info = {
        'name': '阿石创',
        'mcap': 45.0,  # 45亿小票
        'turnover': 4.5  # 换手率4.5%
    }
    
    print(f"\n股票信息:")
    print(f"  名称: {stock_info['name']}")
    print(f"  市值: {stock_info['mcap']}亿")
    print(f"  换手率: {stock_info['turnover']}%")
    
    # 计算V7.5评分
    print(f"\n--- V7.5 评分计算 ---")
    v75_result = calc_dual_layer_score_v75(df, ts_code=ts_code, stock_info=stock_info, theme='AI算力链')
    print(f"V7总评分: {v75_result.get('V7总评分', 'N/A')}")
    print(f"趋势评分: {v75_result.get('趋势评分', 'N/A')}")
    print(f"资金评分: {v75_result.get('资金评分', 'N/A')}")
    print(f"结构评分: {v75_result.get('结构评分', 'N/A')}")
    
    # 计算开仓分
    print(f"\n--- 开仓分 V9.7 计算 ---")
    open_score, structure_type, recommendation = calc_hot_money_open_score_v9(
        v75_result, df, stock_info, theme='AI算力链'
    )
    print(f"\n开仓评分: {open_score}")
    print(f"结构类型: {structure_type}")
    print(f"推荐理由: {recommendation}")
    
    return open_score, structure_type, recommendation, v75_result

if __name__ == "__main__":
    test_stock_open_score()
