# -*- coding: utf-8 -*-
"""测试策略筛选条件"""
import tushare_quant
import pandas as pd

# 获取市场数据
market = tushare_quant.get_market()
print(f'市场数据: {len(market)} 只股票')

# 测试 strategy 函数
count = 0
for idx, row in market.head(100).iterrows():
    ts_code = row['ts_code']
    df = tushare_quant.get_hist_data(ts_code)
    if df is not None and len(df) >= 80:
        ok = tushare_quant.strategy(df, ts_code, 50)  # emotion_stage=50
        if ok:
            name = row['name']
            print(f'命中: {ts_code} {name}')
            count += 1

print(f'\n前100只股票中，有 {count} 只满足策略条件')
