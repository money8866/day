# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

import pandas as pd
import numpy as np
import wave2_pattern_scanner as scanner

detector = scanner.WavePatternDetector()

code = '001389.SZ'
name = '广合科技'

df = detector.load_data(code, lookback=500)
closes = df['close'].values
volumes = df['vol'].values
n = len(df)

# 常量
SURGE_DAYS = 8
SURGE_MIN = 0.20
SIDEWAYS_PULLBACK_MAX = 0.10
SIDEWAYS_ADJUST_MAX = 15
SIDEWAYS_VOL_MAX = 0.80

# 找一波高点
wave1_candidates = detector._find_recent_wave1(closes, n)

print(f"n={n}, 最后一个bar索引={n-1}")
print(f"最后交易日: {str(df.iloc[-1]['trade_date'])}")
print(f"最后收盘: {closes[-1]:.2f}")
print()

for idx, (wave1_high_idx, _, surge_gain) in enumerate(wave1_candidates[:3]):
    wave1_high_price = closes[wave1_high_idx]
    wave1_high_date = str(df.iloc[wave1_high_idx]['trade_date'])
    
    print(f"\n候选{idx}: {wave1_high_date} 一波高点={wave1_high_price:.2f}")
    
    post_high = closes[wave1_high_idx:]
    low_after_high = post_high.min()
    pullback_pct = (wave1_high_price - low_after_high) / wave1_high_price
    low_pos = int(np.argmin(post_high))
    adjust_days = low_pos
    
    print(f"  pullback_pct={pullback_pct*100:.2f}%, low_pos={low_pos}, adjust_days={adjust_days}")
    print(f"  条件1: {pullback_pct*100:.1f}% < 10%? {'✅' if pullback_pct < SIDEWAYS_PULLBACK_MAX else '❌'}")
    print(f"  条件2: {adjust_days} <= 15? {'✅' if adjust_days <= SIDEWAYS_ADJUST_MAX else '❌'}")
    
    if not (pullback_pct < SIDEWAYS_PULLBACK_MAX and adjust_days <= SIDEWAYS_ADJUST_MAX):
        print("  → 跳过（不符合回调条件）")
        continue
    
    # 量比
    vol_base_start = max(0, wave1_high_idx - 60)
    base_vol = volumes[vol_base_start:wave1_high_idx].mean() if wave1_high_idx > 0 else volumes.mean()
    vol_ratio = post_high[:adjust_days + 1].mean() / base_vol if base_vol > 0 else 1.0
    print(f"  量比: vol_ratio={vol_ratio:.4f} (base_vol={base_vol:.2f}, 求均值范围: 0 ~ {adjust_days})")
    print(f"  量比 < 0.80? {'✅' if vol_ratio < SIDEWAYS_VOL_MAX else '❌'}")
    
    if vol_ratio >= SIDEWAYS_VOL_MAX:
        print("  → 跳过（量比不符合）")
        continue
    
    entry_idx = wave1_high_idx + low_pos
    print(f"  标准入场索引: {entry_idx}, 日期: {str(df.iloc[entry_idx]['trade_date'])}")
    
    # 震荡蓄力突破检查
    print(f"\n  震荡蓄力突破检查:")
    print(f"  low_pos={low_pos} >= 3? {'✅' if low_pos >= 3 else '❌'}")
    print(f"  (low_pos + 10) < (n - wave1_high_idx)? ({low_pos + 10} < {n - wave1_high_idx})? {'✅' if (low_pos + 10) < (n - wave1_high_idx) else '❌'}")
    
    if low_pos >= 3 and low_pos <= SIDEWAYS_ADJUST_MAX and (low_pos + 10) < (n - wave1_high_idx):
        after_pullback = closes[entry_idx:]
        print(f"  after_pullback len={len(after_pullback)} >= 15? {'✅' if len(after_pullback) >= 15 else '❌'}")
        
        if len(after_pullback) >= 15:
            cb_high = after_pullback.max()
            cb_low = after_pullback.min()
            cb_range = (cb_high - cb_low) / cb_low
            latest_price = closes[n-1]
            print(f"  cb_range={cb_range*100:.2f}% <= 15%? {'✅' if cb_range <= 0.15 else '❌'}")
            print(f"  closes[n-1]={latest_price:.2f} >= wave1_high_price*0.95={wave1_high_price*0.95:.2f}? {'✅' if latest_price >= wave1_high_price * 0.95 else '❌'}")
            
            # 看下这个从entry到最后的数据
            print(f"  after_pullback 数据预览:")
            for i, p in enumerate(after_pullback[:10]):
                print(f"    +{i}天: {p:.2f}")
            print(f"    ...")
            for i, p in enumerate(after_pullback[-5:], len(after_pullback)-5):
                print(f"    +{i}天: {p:.2f}")
