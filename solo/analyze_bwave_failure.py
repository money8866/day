"""
分析B浪策略失败案例
=====================
详细分析几个+10d收益为负的信号，找出共同特征。
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bwave_strategy import (
    get_data, detect_awave, detect_bwave, detect_bwave_relaxed,
    check_launch_signal, detect_bwave_divergence,
    calc_bwave_score, calc_divergence_score
)
import pandas as pd

# 失败案例列表（+10d收益为负）
failed_cases = [
    ('603061.SH', -11.8),  # +10d -11.8%
    ('605060.SH', -0.7),   # +10d -0.7%
    ('601872.SH', -3.13),  # +10d -3.13%
]

for ts_code, ret_10d in failed_cases:
    print(f'=== {ts_code} (+10d={ret_10d}%) ===')
    
    df = get_data(ts_code)
    if df is None:
        print(f'无法获取{ts_code}数据\n')
        continue
    
    print(f'数据长度: {len(df)}')
    print(f'最新日期: {df.iloc[-1]["trade_date"]}')
    print()
    
    # 检测A浪
    awave = detect_awave(df)
    if not awave:
        print('未检测到A浪\n')
        continue
    
    print(f'A浪: {awave["start_date"]}~{awave["end_date"]} 涨幅={awave["gain"]}% 持续{awave["duration"]}天')
    print(f'A浪评分: {awave["score"]}分')
    print()
    
    # 检测B浪（严格版）
    bwave = detect_bwave(df, awave)
    bwave_r = detect_bwave_relaxed(df, awave) if not bwave else None
    
    if bwave:
        print(f'B浪(严格): {bwave["start_date"]}~{bwave["low_date"]} 回调{bwave["drop"]}%')
        print(f'  B浪持续{bwave["duration"]}天 缩量{bwave["vol_shrink_ratio"]} ATR降{bwave["atr_drop"]}%')
        print(f'  B浪评分: {bwave["score"]}分')
        print()
    elif bwave_r:
        print(f'B浪(放宽): {bwave_r["start_date"]}~{bwave_r["low_date"]} 回调{bwave_r["drop"]}%')
        print()
    else:
        print('未检测到B浪\n')
        continue
    
    # 使用检测到的B浪（严格版优先）
    bwave_used = bwave if bwave else bwave_r
    
    # 检测启动信号
    launch = check_launch_signal(df, awave, bwave_used)
    if launch:
        score = calc_bwave_score(awave, bwave_used, launch)
        print(f'启动信号: {launch["launch_date"]} 评分={score["total"]}分')
        print(f'  距A高={launch["dist_to_a_high"]}% 反弹={launch["b_recovery"]}%')
        print(f'  MACD金叉={launch["macd_golden"]} 放量={launch["vol_surge"]}')
        print()
    else:
        print('未检测到启动信号')
        
    # 检测底背离
    div = detect_bwave_divergence(df, awave, bwave_used)
    if div:
        s = calc_divergence_score(awave, bwave_used, div)
        print(f'底背离信号: 评分={s["total"]}分')
        print(f'  DIF抬高={div.get("dif_up", 0)} RSI={div["rsi6"]}')
        print()
    else:
        print('未检测到底背离')
    
    print('-' * 80)
    print()
