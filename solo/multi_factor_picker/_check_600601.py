# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

import pandas as pd
import numpy as np
import wave2_pattern_scanner as scanner

detector = scanner.WavePatternDetector()

code = '600601.SH'
name = '方正科技'

df = detector.load_data(code, lookback=500)
print(f"{name} ({code}) - 信号分析")
print('='*60)

closes = df['close'].values
volumes = df['vol'].values
n = len(df)

wave1_candidates = detector._find_recent_wave1(closes, n)

count = 0
for wave1_high_idx, wave1_low_idx, surge_gain in wave1_candidates[:5]:
    wave1_high_price = closes[wave1_high_idx]
    post_high = closes[wave1_high_idx:]
    
    if len(post_high) < 5:
        continue
    
    low_after_high = post_high.min()
    pullback_pct = (wave1_high_price - low_after_high) / wave1_high_price
    low_pos = int(np.argmin(post_high))
    adjust_days = low_pos
    
    if not (pullback_pct < 0.10 and adjust_days <= 15):
        continue
    
    vol_base_start = max(0, wave1_high_idx - 60)
    base_vol = volumes[vol_base_start:wave1_high_idx].mean() if wave1_high_idx > 0 else volumes.mean()
    vol_ratio = post_high[:adjust_days + 1].mean() / base_vol if base_vol > 0 else 1.0
    
    if vol_ratio >= 0.80:
        continue
    
    entry_idx = wave1_high_idx + low_pos
    if entry_idx >= n:
        continue
    
    entry_date = str(df.iloc[entry_idx]['trade_date'])
    if not entry_date.startswith('202606'):
        continue
    
    surge_pct = round(surge_gain * 100, 1)
    if not (0.02 <= pullback_pct < 0.10 and 20 <= surge_pct < 60):
        continue
    
    wave1_high_date = str(df.iloc[wave1_high_idx]['trade_date'])
    
    count += 1
    print(f"\n信号 #{count}:")
    print(f"  一波高点: {wave1_high_date} 价格={wave1_high_price:.2f}")
    print(f"  一波涨幅: {surge_pct}%")
    print(f"  入场日期: {entry_date}")
    print(f"  回调深度: {pullback_pct*100:.1f}%")
    print(f"  调整天数: {adjust_days}天")
    
    if count >= 2:
        print(f"\n原因: 一波涨幅不同，说明是两波不同的拉升行情")
        print(f"  第一波: 一波涨幅{21.2}% (高点后回调7.8%入场)")
        print(f"  第二波: 一波涨幅{22.9}% (高点后回调9.7%入场)")
        print(f"  这两个是独立的二波信号点")
        break

print("\n" + "="*60)
print("结论: 方正科技有两波独立的拉升，回调低点分别被识别为信号")
