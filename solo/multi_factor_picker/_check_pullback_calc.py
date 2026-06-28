# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

import pandas as pd
import numpy as np
import wave2_pattern_scanner as scanner

detector = scanner.WavePatternDetector()

code = '600458.SH'
name = '时代新材'

df = detector.load_data(code, lookback=500)

closes = df['close'].values
volumes = df['vol'].values
n = len(df)

target_date = '20260624'
entry_idx = None
for i in range(n):
    if str(df.iloc[i]['trade_date']) == target_date:
        entry_idx = i
        break

print(f"{name} ({code}) - 所有一波高点分析")
print("="*70)
print(f"入场点: {target_date} 价格={closes[entry_idx]:.2f}")
print()

wave1_candidates = detector._find_recent_wave1(closes, n)

for idx, (wave1_high_idx, wave1_low_idx, surge_gain) in enumerate(wave1_candidates[:5], 1):
    wave1_high_price = closes[wave1_high_idx]
    wave1_high_date = str(df.iloc[wave1_high_idx]['trade_date'])
    
    if entry_idx <= wave1_high_idx:
        print(f"候选{idx}: {wave1_high_date} 高点={wave1_high_price:.2f} 涨幅={surge_gain*100:.1f}% [入场点前，跳过]")
        continue
    
    post_high = closes[wave1_high_idx:entry_idx+1]
    low_after_high = post_high.min()
    low_pos = int(np.argmin(post_high))
    low_date = str(df.iloc[wave1_high_idx + low_pos]['trade_date'])
    
    pullback_pct = (wave1_high_price - low_after_high) / wave1_high_price
    adjust_days = low_pos
    
    # 计算量比
    vol_base_start = max(0, wave1_high_idx - 60)
    base_vol = volumes[vol_base_start:wave1_high_idx].mean()
    vol_ratio = post_high[:adjust_days + 1].mean() / base_vol if base_vol > 0 else 1.0
    
    print(f"候选{idx}: {wave1_high_date} 高点={wave1_high_price:.2f} 涨幅={surge_gain*100:.1f}%")
    print(f"        回调低点: {low_date} 价格={low_after_high:.2f} 回调={pullback_pct*100:.1f}% 调整{adjust_days}天")
    print(f"        量比={vol_ratio:.2f}")
    print()
