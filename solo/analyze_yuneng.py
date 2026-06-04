#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
分析豫能控股为什么没有被选为中军
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
    print("分析豫能控股为什么没有被选为中军")
    print("=" * 80)
    
    # 豫能控股代码
    code = "001896.SZ"
    name = "豫能控股"
    
    print(f"\n分析股票: {name} ({code})")
    
    # 1. 检查是否属于电力链主题
    print("\n[1/6] 检查是否属于电力链主题...")
    hot_themes = theme_score.load_theme_json()
    dc_df = theme_score.get_dc_members()
    stock_basic = theme_score.get_stock_basic()
    
    theme_stock_map, name_map_basic, stock_industry, stock_concepts = theme_score.match_theme_stocks(hot_themes, dc_df, stock_basic)
    
    in_power_chain = False
    power_chain_stocks = theme_stock_map.get("电力链", {})
    
    print(f"   电力链成份股数量: {len(power_chain_stocks)}")
    
    if code in power_chain_stocks:
        print(f"   ✅ 豫能控股属于电力链主题")
        in_power_chain = True
    else:
        print(f"   ❌ 豫能控股不属于电力链主题")
        print(f"   电力链成份股列表:")
        for s_code, s_name in list(power_chain_stocks.items())[:20]:
            print(f"      {s_code} {s_name}")
    
    if not in_power_chain:
        print("\n❌ 主要原因: 豫能控股不在电力链主题的成份股列表中!")
        return
    
    # 2. 检查K线数据
    print("\n[2/6] 获取K线数据...")
    today = theme_score.TRADE_DATE
    start_30d = (datetime.strptime(today, '%Y%m%d') - timedelta(days=100)).strftime('%Y%m%d')
    
    kline_df = theme_score.get_daily_kline([code], start_30d, today)
    
    if code not in kline_df['ts_code'].values:
        print(f"   ❌ 没有找到{name}的K线数据")
        return
    
    df = kline_df[kline_df['ts_code'] == code].sort_values('trade_date').copy()
    print(f"   成功获取 {len(df)} 天数据")
    
    if len(df) < 60:
        print(f"   ❌ 数据不足60天，无法满足中军条件")
        return
    
    print(f"   最新日期: {df['trade_date'].iloc[-1]}")
    
    # 3. 检查8个条件
    print("\n[3/6] 检查8个中军条件...")
    
    closes = df['close'].astype(float).values
    highs = df['high'].astype(float).values
    lows = df['low'].astype(float).values
    pcts = df['pct_chg'].astype(float).values
    amounts = df['amount'].astype(float).values
    
    last = len(closes) - 1
    close = closes[last]
    
    # 计算均线
    ma5_vals = df['ma5'].astype(float).values
    ma10_vals = df['ma10'].astype(float).values
    ma20_vals = df['ma20'].astype(float).values
    
    ma5 = ma5_vals[last]
    ma10 = ma10_vals[last]
    ma20 = ma20_vals[last]
    
    print(f"\n   当前股价: {close:.2f}")
    print(f"   MA5: {ma5:.2f}, MA10: {ma10:.2f}, MA20: {ma20:.2f}")
    
    # 条件1: 主题类型为中期趋势或短线主线
    print(f"\n   条件1: 主题类型 = 中期趋势/短线主线 (电力链昨天是短线主线)")
    print(f"   ✅ 满足")
    
    # 条件2: avg_amount_20 >= 12亿
    recent_20 = df.iloc[-21:-1] if len(df) >= 21 else df
    avg_amount_20 = recent_20['amount'].astype(float).mean() / 100000  # 千元→亿
    print(f"\n   条件2: 20日平均成交额 >= 12亿")
    print(f"   实际值: {avg_amount_20:.2f}亿")
    if avg_amount_20 >= 12:
        print(f"   ✅ 满足")
    else:
        print(f"   ❌ 不满足")
    
    # 条件3: close > MA5 > MA10 > MA20
    print(f"\n   条件3: close > MA5 > MA10 > MA20")
    cond3 = close > ma5 and ma5 > ma10 and ma10 > ma20
    print(f"   {close:.2f} > {ma5:.2f} > {ma10:.2f} > {ma20:.2f} ? {cond3}")
    if cond3:
        print(f"   ✅ 满足")
    else:
        print(f"   ❌ 不满足")
    
    # 条件4: MA20向上
    if len(ma20_vals) >= 10:
        ma20_slope = (ma20_vals[-1] - ma20_vals[-10]) / 10 / ma20_vals[-10] * 100
    else:
        ma20_slope = 0
    print(f"\n   条件4: MA20斜率 > 0")
    print(f"   实际值: {ma20_slope:.4f}%")
    if ma20_slope > 0:
        print(f"   ✅ 满足")
    else:
        print(f"   ❌ 不满足")
    
    # 条件5: close >= HHV60 * 0.90
    if len(closes) >= 60:
        hhv60 = max(closes[-60:])
    else:
        hhv60 = max(closes)
    hhv60_ratio = close / hhv60 * 100
    print(f"\n   条件5: 收盘价 >= 60日最高价 * 0.90")
    print(f"   60日最高: {hhv60:.2f}, 当前: {close:.2f}, 比例: {hhv60_ratio:.1f}%")
    if hhv60_ratio >= 90:
        print(f"   ✅ 满足")
    else:
        print(f"   ❌ 不满足")
    
    # 条件6: RS20 >= 5
    if len(closes) >= 21:
        ret_20 = (closes[-1] - closes[-21]) / closes[-21] * 100
    else:
        ret_20 = 0
    
    # 获取电力链主题的20日涨幅
    results, _ = theme_score.run_theme_analysis()
    power_theme = None
    for r in results:
        if r['theme'] == '电力链':
            power_theme = r
            break
    
    theme_ret_20 = 0
    if power_theme:
        trend_detail = power_theme.get('trend_detail', {})
        theme_ret_20 = trend_detail.get('avg_ret_20', 0)
    
    rs20 = ret_20 - theme_ret_20
    print(f"\n   条件6: RS20 >= 5 (个股20日涨幅 - 主题20日涨幅)")
    print(f"   个股20日涨幅: {ret_20:.2f}%, 主题20日涨幅: {theme_ret_20:.2f}%, RS20: {rs20:.2f}")
    if rs20 >= 5:
        print(f"   ✅ 满足")
    else:
        print(f"   ❌ 不满足")
    
    # 条件7: 20日涨停数 <= 2
    recent_20_pcts = df['pct_chg'].astype(float).values[-21:-1] if len(df) >= 21 else df['pct_chg'].astype(float).values
    zt_count = sum(1 for p in recent_20_pcts if p >= 9.5)
    print(f"\n   条件7: 20日涨停数 <= 2")
    print(f"   实际值: {zt_count}个")
    if zt_count <= 2:
        print(f"   ✅ 满足")
    else:
        print(f"   ❌ 不满足")
    
    # 条件8: 近5日未跌破MA20
    broke_ma20 = False
    if 'low' in df.columns and len(df) >= 6:
        recent_5_low = df['low'].astype(float).values[-6:-1]
        recent_5_ma20 = ma20_vals[-6:-1]
        for i, (low, ma20_val) in enumerate(zip(recent_5_low, recent_5_ma20)):
            if low < ma20_val:
                date = df['trade_date'].values[-6+i]
                print(f"      {date}: 最低价 {low:.2f} < MA20 {ma20_val:.2f}")
                broke_ma20 = True
    
    print(f"\n   条件8: 近5日未跌破MA20")
    if not broke_ma20:
        print(f"   ✅ 满足")
    else:
        print(f"   ❌ 不满足，有跌破")
    
    # 4. 与华能国际对比
    print("\n[4/6] 与华能国际对比...")
    huaneng_code = "600011.SH"
    
    if huaneng_code not in kline_df['ts_code'].values:
        huaneng_kline = theme_score.get_daily_kline([huaneng_code], start_30d, today)
        huaneng_df = huaneng_kline[huaneng_kline['ts_code'] == huaneng_code].sort_values('trade_date')
    else:
        huaneng_df = kline_df[kline_df['ts_code'] == huaneng_code].sort_values('trade_date')
    
    if len(huaneng_df) > 0:
        huaneng_closes = huaneng_df['close'].astype(float).values
        huaneng_amounts = huaneng_df['amount'].astype(float).values
        huaneng_ma20_vals = huaneng_df['ma20'].astype(float).values
        
        huaneng_recent_20 = huaneng_df.iloc[-21:-1] if len(huaneng_df) >= 21 else huaneng_df
        huaneng_avg_amount_20 = huaneng_recent_20['amount'].astype(float).mean() / 100000
        
        print(f"\n   {'指标':<20} {'豫能控股':<15} {'华能国际':<15}")
        print(f"   { '-'*20 } { '-'*15 } { '-'*15 }")
        print(f"   {'收盘价':<20} {close:<15.2f} {huaneng_closes[-1]:<15.2f}")
        print(f"   {'20日平均成交额':<20} {avg_amount_20:<15.2f} {huaneng_avg_amount_20:<15.2f}")
        print(f"   {'MA20斜率':<20} {ma20_slope:<15.4f} {(huaneng_ma20_vals[-1]-huaneng_ma20_vals[-10])/10/huaneng_ma20_vals[-10]*100 if len(huaneng_ma20_vals)>=10 else 0:<15.4f}")
        print(f"   {'HHV60比例':<20} {hhv60_ratio:<15.1f} {min(100, huaneng_closes[-1]/max(huaneng_closes[-60:])*100):<15.1f}")
    
    # 5. 输出详细数据
    print("\n[5/6] 输出详细K线数据...")
    print("\n   最近10天数据:")
    print(f"   {'日期':<12} {'开盘':<8} {'最高':<8} {'最低':<8} {'收盘':<8} {'涨跌幅':<8} {'成交额(亿)':<10} {'MA5':<8} {'MA10':<8} {'MA20':<8}")
    print(f"   { '-'*12 } { '-'*8 } { '-'*8 } { '-'*8 } { '-'*8 } { '-'*8 } { '-'*10 } { '-'*8 } { '-'*8 } { '-'*8 }")
    
    for i in range(max(0, last-10), last+1):
        date = df['trade_date'].iloc[i]
        open_p = df['open'].iloc[i]
        high = df['high'].iloc[i]
        low = df['low'].iloc[i]
        close_p = df['close'].iloc[i]
        pct = df['pct_chg'].iloc[i]
        amt = df['amount'].iloc[i] / 100000
        ma5 = df['ma5'].iloc[i]
        ma10 = df['ma10'].iloc[i]
        ma20 = df['ma20'].iloc[i]
        print(f"   {date:<12} {open_p:<8.2f} {high:<8.2f} {low:<8.2f} {close_p:<8.2f} {pct:<8.2f} {amt:<10.2f} {ma5:<8.2f} {ma10:<8.2f} {ma20:<8.2f}")
    
    print("\n[6/6] 总结...")
    print("\n❌ 豫能控股没有被选为中军的可能原因:")
    print("   1. 不在电力链主题成份股列表中（可能是主题配置问题）")
    print("   2. 成交额不足（豫能控股的成交额通常比华能国际小很多）")
    print("   3. 均线形态不符合（MA5/MA10/MA20可能不是多头排列）")
    print("   4. 相对强度不够（RS20 < 5）")
    
    print("\n✅ 建议检查:")
    print("   1. 查看 theme.json 中电力链主题的配置")
    print("   2. 查看豫能控股是否在电力链的成份股列表中")
    print("   3. 如果需要，可以放宽某些条件（如成交额阈值）")

if __name__ == '__main__':
    main()
