#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
检查豫能控股在电力链中的排名情况
"""

import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import theme_trend_sentiment_score as theme_score

def main():
    print("=" * 80)
    print("检查豫能控股在电力链中的排名情况")
    print("=" * 80)
    
    # 获取电力链成份股
    hot_themes = theme_score.load_theme_json()
    dc_df = theme_score.get_dc_members()
    stock_basic = theme_score.get_stock_basic()
    
    theme_stock_map, name_map_basic, stock_industry, stock_concepts = theme_score.match_theme_stocks(hot_themes, dc_df, stock_basic)
    
    power_stocks = theme_stock_map.get("电力链", {})
    print(f"\n电力链成份股数量: {len(power_stocks)}")
    
    # 获取K线数据
    today = theme_score.TRADE_DATE
    start_30d = (datetime.strptime(today, '%Y%m%d') - timedelta(days=100)).strftime('%Y%m%d')
    
    codes = list(power_stocks.keys())
    kline_df = theme_score.get_daily_kline(codes, start_30d, today)
    
    # 计算每只股票的20日平均成交额
    stock_amounts = []
    target_code = "001896.SZ"
    
    for code in codes:
        if code not in kline_df['ts_code'].values:
            continue
        
        df = kline_df[kline_df['ts_code'] == code].sort_values('trade_date')
        if len(df) < 25:
            continue
        
        recent_20 = df.iloc[-21:-1] if len(df) >= 21 else df
        avg_amount_20 = recent_20['amount'].astype(float).mean() / 100000  # 千元→亿
        
        name = power_stocks.get(code, code)
        stock_amounts.append((code, name, avg_amount_20))
    
    # 按成交额排序
    stock_amounts.sort(key=lambda x: -x[2])
    
    print(f"\n电力链股票成交额排名（前20名）:")
    print(f"{'排名':<6}{'代码':<15}{'名称':<15}{'20日平均成交额(亿)':<20}")
    print("-" * 60)
    
    target_rank = None
    for i, (code, name, amount) in enumerate(stock_amounts[:20], 1):
        marker = ""
        if code == target_code:
            marker = " ← 豫能控股"
            target_rank = i
        name_str = str(name)
        print(f"{i:<6}{code:<15}{name_str:<15}{amount:<20.2f}{marker}")
    
    # 计算前30%的阈值
    top_30_pct_index = max(1, int(len(stock_amounts) * 0.3))
    threshold = stock_amounts[top_30_pct_index - 1][2] if stock_amounts else 0
    
    print(f"\n总股票数: {len(stock_amounts)}")
    print(f"前30%的阈值（排名第{top_30_pct_index}名）: {threshold:.2f}亿")
    print(f"豫能控股排名: {target_rank}")
    print(f"豫能控股20日平均成交额: {next((x[2] for x in stock_amounts if x[0] == target_code), 0):.2f}亿")
    
    if target_rank and target_rank <= top_30_pct_index:
        print(f"✅ 豫能控股在电力链中排名前30%")
    else:
        print(f"❌ 豫能控股不在电力链中排名前30%")

if __name__ == '__main__':
    main()
