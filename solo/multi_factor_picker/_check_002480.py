# -*- coding: utf-8 -*-
"""
检查时代新材在20260624附近的详细情况
"""
import sys, os
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

import pandas as pd
import numpy as np

import wave2_pattern_scanner as scanner

detector = scanner.WavePatternDetector()

code = '002480.SZ'
name = '时代新材'

print(f"{name} ({code}) - 详细分析")
print('='*60)

df = detector.load_data(code, lookback=500)
if df is None or len(df) < 60:
    print("数据不足")
    exit()

print(f"数据范围: {df.iloc[0]['trade_date']} ~ {df.iloc[-1]['trade_date']}")
print(f"数据条数: {len(df)}")

# 找最近10天的数据
for i in range(-10, 0):
    row = df.iloc[i]
    print(f"\n{row['trade_date']}: 收盘={row['close']:.2f} 涨跌幅={row.get('pct_chg', 0):.2f}%")

print("\n" + "="*60)
print("扫描最近一波拉升后的回调情况:")

closes = df['close'].values
n = len(df)

# 找最近一波高点
for i in range(n-1, max(0, n-60), -1):
    high_idx = i
    high_price = closes[high_idx]
    high_date = df.iloc[high_idx]['trade_date']
    
    # 找高点后的低点
    post_high = closes[high_idx:]
    if len(post_high) < 3:
        continue
    
    low_after = post_high.min()
    pullback_pct = (high_price - low_after) / high_price
    
    # 一波涨幅（从低点算起）
    pre_low_idx = max(0, high_idx - 30)
    pre_low = closes[pre_low_idx:high_idx].min()
    surge_gain = (high_price - pre_low) / pre_low
    
    # 只看符合条件的
    if pullback_pct >= 0.02 and pullback_pct < 0.15 and surge_gain >= 0.15:
        print(f"\n最近高点: {high_date} 价格={high_price:.2f}")
        print(f"  一波涨幅: {surge_gain*100:.1f}%")
        print(f"  回调深度: {pullback_pct*100:.1f}%")
        
        # 评分
        low_pos = np.argmin(post_high)
        entry_idx = high_idx + low_pos
        entry_date = df.iloc[entry_idx]['trade_date']
        adjust_days = low_pos
        
        print(f"  入场点: {entry_date} (回调第{adjust_days}天)")
        
        # 计算评分
        prev_row = df.iloc[entry_idx - 1] if entry_idx > 0 else None
        new_high_confirmed = False
        post_high_all = closes[high_idx:entry_idx + 1]
        if len(post_high_all) > 1 and post_high_all.max() > high_price:
            new_high_confirmed = True
        
        gap_to_peak = (high_price - closes[entry_idx]) / closes[entry_idx]
        score_result = detector.scorer.score(
            df.iloc[entry_idx], prev_row,
            wave1_gain_pct=round(surge_gain * 100, 1),
            new_high_confirmed=new_high_confirmed,
            new_high_pullback=False,
            is_higher_low=True,
            pattern_type='强势横盘',
            gap_to_peak_pct=gap_to_peak,
            pullback_pct=pullback_pct,
            is_deep_long_consolidation=False,
            limitup_score=0,
            volume_recovery_score=0
        )
        
        divs = detector.scorer.check_divergence(df, entry_idx)
        for key, div in divs.items():
            if div.get('found'):
                score_result['total'] += div['pts']
        
        dmi_cross = detector.scorer.check_dmi_crossover(df, entry_idx)
        if dmi_cross.get('found'):
            score_result['total'] += dmi_cross['pts']
        
        bonus_pts, bonus_desc = detector._board_bonus(code, '强势横盘')
        score_result['total'] += bonus_pts
        
        print(f"  评分: {score_result['total']}分 (阈值20分)")
        print(f"  条件: 回调{pullback_pct*100:.1f}% (需2-10%) + 一波{surge_gain*100:.1f}% (需20-60%)")
        
        if score_result['total'] >= 20:
            print(f"  ✅ 有信号!")
        else:
            print(f"  ❌ 无信号")
        
        break
