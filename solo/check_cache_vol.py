#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
模拟选股程序的环境，检查豫能控股
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
    print("模拟选股程序环境检查豫能控股")
    print("=" * 80)
    
    code = "001896.SZ"
    today = theme_score.TRADE_DATE
    start_30d = (datetime.strptime(today, '%Y%m%d') - timedelta(days=100)).strftime('%Y%m%d')
    
    print(f"\n交易日: {today}")
    print(f"K线区间: {start_30d} ~ {today}")
    
    # 直接从缓存文件读取
    cache_dir = r"D:\mystock\solo\cache_backbone_tushare"
    cache_file = os.path.join(cache_dir, f"{code}.csv")
    
    if os.path.exists(cache_file):
        df = pd.read_csv(cache_file)
        df = df[df['trade_date'].astype(str) <= today]
        df = df.sort_values('trade_date').tail(70)
        print(f"\n从缓存读取 {len(df)} 天数据")
        print(f"最新日期: {df['trade_date'].iloc[-1]}")
        
        vols = df['vol'].astype(float).values
        print(f"\n近5日成交量:")
        for i in range(-5, 0):
            print(f"  第{i}天: {vols[i]/10000:.2f}万手")
        
        if len(vols) >= 23:
            vol_3 = vols[-3:].mean()
            vol_20 = vols[-20:].mean()
            vol_ratio = vol_3 / vol_20 if vol_20 > 0 else 0
            print(f"\n近3日成交量均值: {vol_3/10000:.2f}万手")
            print(f"近20日成交量均值: {vol_20/10000:.2f}万手")
            print(f"放大比例: {vol_ratio:.2f}倍")
            print(f"是否>=1.2: {vol_ratio >= 1.2}")
    else:
        print(f"\n缓存文件不存在: {cache_file}")

if __name__ == '__main__':
    main()
