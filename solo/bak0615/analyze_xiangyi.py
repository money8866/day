#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
分析祥生医疗为什么会误判
"""
import sys
import pandas as pd
import numpy as np
sys.path.append(r'd:\mystock\solo')

from theme_rotation_analysis_final import (
    get_trade_dates, 
    get_stock_history, 
    calculate_ma_and_biased,
    calculate_second_wave_probability,
    calculate_rebound_probability
)

print("=" * 100)
print("祥生医疗(688358.SH)数据分析")
print("=" * 100)

# 获取数据
trade_dates = get_trade_dates(30)
df = get_stock_history('688358.SH', 30)

if not df.empty:
    print(f"\n获取到 {len(df)} 天数据")
    
    # 显示最近10天数据
    print("\n最近10天数据:")
    print(df[['trade_date', 'close', 'pct_chg', 'amount']].tail(10))
    
    # 计算均线
    ma_data = calculate_ma_and_biased(df)
    
    if ma_data:
        print("\n均线和乖离率:")
        print(f"  5日均线: {ma_data['ma5']:.2f}")
        print(f"  10日均线: {ma_data['ma10']:.2f}")
        print(f"  20日均线: {ma_data['ma20']:.2f}")
        print(f"  当前价格: {ma_data['current_price']:.2f}")
        print(f"  5日乖离: {ma_data['ma5_biased']:.2f}%")
        print(f"  20日乖离: {ma_data['ma20_biased']:.2f}%")
        print(f"  5日均线斜率: {ma_data['ma5_slope']:.2f}")
        print(f"  20日均线斜率: {ma_data['ma20_slope']:.2f}")
        print(f"  量比: {ma_data['volume_ratio']:.2f}")
        
        # 查看最近5天的涨跌
        recent_5 = df.tail(5)
        print("\n最近5天涨跌:")
        for i, (idx, row) in enumerate(recent_5.iterrows(), 1):
            print(f"  第{5-i+1}天: {row['trade_date']} 涨跌 {row['pct_chg']:+.2f}%")
        
        print(f"\n最近5日累计: {recent_5['pct_chg'].sum():+.2f}%")
        print(f"最近10日累计: {df.tail(10)['pct_chg'].sum():+.2f}%")
        print(f"最近20日累计: {df.tail(20)['pct_chg'].sum():+.2f}%")
        
        # 调用二波概率计算
        print("\n二波概率计算:")
        prob, level, reasons = calculate_second_wave_probability(ma_data, df)
        print(f"  二波概率: {prob}% [{level}]")
        print(f"  原因: {reasons}")
        
        # 调用回升概率计算
        print("\n回升概率计算:")
        prob, level, reasons = calculate_rebound_probability(ma_data, df, 0)
        print(f"  回升概率: {prob}% [{level}]")
        print(f"  原因: {reasons}")
        
        # 分析第一天调整
        print("\n第一天调整识别:")
        recent_3 = df.tail(3)
        print(f"  第-1天: {recent_3.iloc[-2]['trade_date']} {recent_3.iloc[-2]['pct_chg']:+.2f}%")
        print(f"  第0天: {recent_3.iloc[-1]['trade_date']} {recent_3.iloc[-1]['pct_chg']:+.2f}%")
        
        if recent_3.iloc[-1]['pct_chg'] < 0:
            # 前一天是涨的
            if recent_3.iloc[-2]['pct_chg'] > 0:
                print("  ⚠️  这是第一天从上涨转为下跌！")
                print("  💡  建议: 等2-3天确认后再判断")
            else:
                print("  调整已经持续多天")
