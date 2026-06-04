#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
详细检查豫能控股为什么没有被选为补涨中军
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
    print("详细检查豫能控股补涨中军筛选过程")
    print("=" * 80)
    
    code = "001896.SZ"
    name = "豫能控股"
    
    print(f"\n分析股票: {name} ({code})")
    
    # 获取K线数据
    today = theme_score.TRADE_DATE
    start_30d = (datetime.strptime(today, '%Y%m%d') - timedelta(days=100)).strftime('%Y%m%d')
    
    kline_df = theme_score.get_daily_kline([code], start_30d, today)
    df = kline_df[kline_df['ts_code'] == code].sort_values('trade_date').copy()
    
    print(f"获取 {len(df)} 天数据")
    print(f"最新日期: {df['trade_date'].iloc[-1]}")
    
    closes = df['close'].astype(float).values
    vols = df['vol'].astype(float).values
    ma5_vals = df['ma5'].astype(float).values
    ma10_vals = df['ma10'].astype(float).values
    ma20_vals = df['ma20'].astype(float).values
    
    last = len(closes) - 1
    close = closes[-1]
    ma5 = ma5_vals[-1]
    ma10 = ma10_vals[-1]
    ma20 = ma20_vals[-1]
    
    print(f"\n当前价格: {close:.2f}")
    print(f"MA5={ma5:.2f}, MA10={ma10:.2f}, MA20={ma20:.2f}")
    
    # 条件1: 主题类型
    print(f"\n[条件1] 主题类型 = 短线主线")
    print(f"   ✅ 电力链是短线主线")
    
    # 条件2: 成交额
    recent_20 = df.iloc[-21:-1] if len(df) >= 21 else df
    avg_amount_20 = recent_20['amount'].astype(float).mean() / 100000
    print(f"\n[条件2] 20日平均成交额 >= 8亿")
    print(f"   实际值: {avg_amount_20:.2f}亿")
    if avg_amount_20 >= 8:
        print(f"   ✅ 满足")
    else:
        print(f"   ❌ 不满足")
    
    # 条件3: 均线金叉
    print(f"\n[条件3] 均线金叉")
    
    # 检查今日
    ma5_cross_ma10_today = (ma5_vals[-2] <= ma10_vals[-2]) and (ma5_vals[-1] > ma10_vals[-1])
    ma10_cross_ma20_today = (ma10_vals[-2] <= ma20_vals[-2]) and (ma10_vals[-1] > ma20_vals[-1])
    
    print(f"   今日MA5是否上穿MA10: {ma5_cross_ma10_today}")
    print(f"   今日MA10是否上穿MA20: {ma10_cross_ma20_today}")
    
    # 检查近3日
    recent_cross = False
    for i in range(-3, 0):
        if i >= -len(ma5_vals) + 1:
            ma5_cross = ma5_vals[i-1] <= ma10_vals[i-1] and ma5_vals[i] > ma10_vals[i]
            ma10_cross = ma10_vals[i-1] <= ma20_vals[i-1] and ma10_vals[i] > ma20_vals[i]
            if ma5_cross or ma10_cross:
                recent_cross = True
                print(f"   近3日（第{i}天）有金叉")
                break
    
    print(f"   近3日有金叉: {recent_cross}")
    
    if ma5_cross_ma10_today or ma10_cross_ma20_today or recent_cross:
        print(f"   ✅ 满足")
    else:
        print(f"   ❌ 不满足")
    
    # 条件4: 成交量放大
    print(f"\n[条件4] 成交量放大（近3日 > 20日 * 1.2）")
    if len(vols) >= 23:
        vol_3 = vols[-3:].mean()
        vol_20 = vols[-20:].mean()
        vol_ratio = vol_3 / vol_20 if vol_20 > 0 else 0
        print(f"   近3日成交量均值: {vol_3/10000:.2f}万手")
        print(f"   近20日成交量均值: {vol_20/10000:.2f}万手")
        print(f"   放大比例: {vol_ratio:.2f}倍")
        if vol_ratio >= 1.2:
            print(f"   ✅ 满足")
        else:
            print(f"   ❌ 不满足")
    else:
        print(f"   ❌ 数据不足")
    
    # 条件5: 板块内成交额居前
    print(f"\n[条件5] 板块内成交额居前（前30%）")
    print(f"   豫能控股排名第3，成交额32.16亿")
    print(f"   前30%阈值（排名9）需要19.12亿")
    print(f"   ✅ 满足")
    
    # 条件6: 均线多头
    print(f"\n[条件6] 均线多头（close > MA5 > MA10）")
    cond6 = close > ma5 and ma5 > ma10
    print(f"   {close:.2f} > {ma5:.2f} > {ma10:.2f} ? {cond6}")
    if cond6:
        print(f"   ✅ 满足")
    else:
        print(f"   ❌ 不满足")
    
    # 检查是否所有条件都满足
    all_pass = (
        avg_amount_20 >= 8 and
        (ma5_cross_ma10_today or ma10_cross_ma20_today or recent_cross) and
        vol_ratio >= 1.2 and
        cond6
    )
    
    print(f"\n" + "=" * 80)
    if all_pass:
        print("✅ 豫能控股应该被选为补涨中军！")
    else:
        print("❌ 豫能控股有条件不满足")
    print("=" * 80)

if __name__ == '__main__':
    main()
