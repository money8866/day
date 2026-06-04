#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
回溯检查20260602豫能控股的补涨中军条件
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
    print("检查20260602豫能控股是否符合补涨中军条件")
    print("=" * 80)
    
    # 设置回溯日期
    backfill_date = "20260602"
    theme_score.TRADE_DATE = backfill_date
    theme_score.START_DATE = (datetime.strptime(backfill_date, "%Y%m%d") - timedelta(days=100)).strftime("%Y%m%d")
    
    code = "001896.SZ"
    name = "豫能控股"
    
    print(f"\n分析股票: {name} ({code})")
    print(f"回溯日期: {backfill_date}")
    
    # 获取K线数据
    start_30d = theme_score.START_DATE
    kline_df = theme_score.get_daily_kline([code], start_30d, backfill_date)
    
    if code not in kline_df['ts_code'].values:
        print(f"   ❌ 没有找到{name}的K线数据")
        return
    
    df = kline_df[kline_df['ts_code'] == code].sort_values('trade_date').copy()
    print(f"   获取 {len(df)} 天数据")
    print(f"   最新日期: {df['trade_date'].iloc[-1]}")
    
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
    print(f"\n[条件3] 均线金叉（MA5上穿MA10或MA10上穿MA20）")
    
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
            print(f"   ❌ 不满足（需要>=1.2倍）")
    else:
        print(f"   ❌ 数据不足")
    
    # 条件5: 板块内成交额居前
    print(f"\n[条件5] 板块内成交额居前（前30%）")
    print(f"   实际值: {avg_amount_20:.2f}亿")
    print(f"   需要检查在电力链中的排名...")
    
    # 检查20260602的电力链成份股排名
    hot_themes = theme_score.load_theme_json()
    dc_df = theme_score.get_dc_members()
    stock_basic = theme_score.get_stock_basic()
    theme_stock_map, _, _, _ = theme_score.match_theme_stocks(hot_themes, dc_df, stock_basic)
    
    power_stocks = theme_stock_map.get("电力链", {})
    power_codes = list(power_stocks.keys())
    
    if power_codes:
        power_kline = theme_score.get_daily_kline(power_codes, start_30d, backfill_date)
        power_amounts = []
        
        for c in power_codes:
            if c not in power_kline['ts_code'].values:
                continue
            temp_df = power_kline[power_kline['ts_code'] == c].sort_values('trade_date')
            if len(temp_df) < 25:
                continue
            temp_recent_20 = temp_df.iloc[-21:-1] if len(temp_df) >= 21 else temp_df
            temp_avg = temp_recent_20['amount'].astype(float).mean() / 100000
            power_amounts.append((c, power_stocks.get(c, c), temp_avg))
        
        power_amounts.sort(key=lambda x: -x[2])
        top_30_pct_index = max(1, int(len(power_amounts) * 0.3))
        threshold = power_amounts[top_30_pct_index - 1][2] if power_amounts else 0
        
        target_rank = None
        for i, (c, n, a) in enumerate(power_amounts, 1):
            if c == code:
                target_rank = i
                break
        
        print(f"   电力链总股票数: {len(power_amounts)}")
        print(f"   前30%阈值（排名{top_30_pct_index}）: {threshold:.2f}亿")
        print(f"   豫能控股排名: {target_rank}")
        
        if target_rank and target_rank <= top_30_pct_index:
            print(f"   ✅ 满足")
        else:
            print(f"   ❌ 不满足（不在前30%）")
    
    # 条件6: 均线多头
    print(f"\n[条件6] 均线多头（close > MA5 > MA10）")
    cond6 = close > ma5 and ma5 > ma10
    print(f"   {close:.2f} > {ma5:.2f} > {ma10:.2f} ? {cond6}")
    if cond6:
        print(f"   ✅ 满足")
    else:
        print(f"   ❌ 不满足")
    
    # 总结
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
        print("❌ 豫能控股有条件不满足，未被选中")
    print("=" * 80)

if __name__ == '__main__':
    main()
