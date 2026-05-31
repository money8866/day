#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查688525.SH的成交额"""
import os, pickle
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")

cache_path = os.path.join(CACHE_DIR, "cache_batch_daily_key_batch_20260529_3111.pkl")

with open(cache_path, 'rb') as f:
    cache_data = pickle.load(f)

df = cache_data['data']

# 检查688525.SH的数据
stock_df = df[df['ts_code'] == '688525.SH'].sort_values('trade_date', ascending=False)
print("688525.SH (佰维存储) 数据:")
print(stock_df[['ts_code', 'trade_date', 'close', 'amount', 'vol', 'pct_chg']])

print(f"\n成交额(amount)单位: 千元")
print(f"最近5日平均成交额 = {stock_df['amount'].head(5).mean():.2f} 千元 = {stock_df['amount'].head(5).mean()/100000000:.4f} 亿元")

# 检查AI终端主题中每只股票的成交额
ai_stocks = ['688525.SH', '300476.SZ', '002475.SZ', '000063.SZ', '601138.SH']
print("\n\nAI终端相关股票最近5日平均成交额:")
for code in ai_stocks:
    sdf = df[df['ts_code'] == code]
    if len(sdf) > 0:
        avg_5d = sdf['amount'].head(5).mean()
        avg_5d_yi = avg_5d / 100000000
        print(f"  {code}: {avg_5d_yi:.4f} 亿元")