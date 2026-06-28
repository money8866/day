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

target_date = '20260624'
entry_idx = None
for i in range(n):
    if str(df.iloc[i]['trade_date']) == target_date:
        entry_idx = i
        break

if entry_idx is None:
    print(f"未找到 {target_date} 数据")
    exit()

print(f"{name} ({code}) - 全面分析")
print("="*70)

# 检查所有一波高点候选
print(f"\n--- 所有一波高点候选（合并前）---")
wave1_candidates_raw = []
for lookback in range(3, min(150, n - 8 - 5)):
    end_idx = n - lookback
    if end_idx < 8:
        continue
    window = closes[end_idx - 8:end_idx + 1]
    low_in_win = np.argmin(window)
    high_in_win = np.argmax(window)
    if high_in_win <= low_in_win:
        continue
    if (high_in_win - low_in_win) > 8 - 2:
        continue
    surge_gain = (window[high_in_win] - window[low_in_win]) / window[low_in_win]
    if surge_gain < 0.20:
        continue
    wave1_high_idx = end_idx - 8 + high_in_win
    wave1_low_idx = end_idx - 8 + low_in_win
    
    if not any(wave1_high_idx == h for h, *_ in wave1_candidates_raw):
        lookback_start = max(0, wave1_low_idx - 200)
        pre_history = closes[lookback_start:wave1_low_idx]
        if len(pre_history) >= 20:
            pre_high = pre_history.max()
            if pre_high > closes[wave1_high_idx] * 1.15:
                continue
        wave1_candidates_raw.append((wave1_high_idx, wave1_low_idx, surge_gain))

wave1_candidates_raw.sort(key=lambda x: (n - x[0]))

# 合并逻辑（v3.5）
merged = []
used = set()
for i_item, (h1, l1, g1) in enumerate(wave1_candidates_raw):
    if i_item in used:
        continue
    best_h, best_l, best_g = h1, l1, g1
    for j, (h2, l2, g2) in enumerate(wave1_candidates_raw):
        if j <= i_item or j in used:
            continue
        if abs(h2 - h1) <= 5:
            used.add(j)
            if closes[h2] > closes[best_h]:
                best_h, best_l, best_g = h2, l2, g2
    used.add(i_item)
    merged.append((best_h, best_l, best_g))
merged.sort(key=lambda x: (n - x[0]))

print(f"合并前候选: {len(wave1_candidates_raw)} 个")
print(f"合并后候选: {len(merged)} 个")
print()

print(f"{'Idx':>5} {'日期':>10} {'价格':>8} {'涨幅':>6} {'合并前':>8}")
print("-"*50)
for idx, (h, l, g) in enumerate(wave1_candidates_raw):
    d = str(df.iloc[h]['trade_date'])
    label = "保留" if any(h == m[0] for m in merged) else "合并"
    print(f"{idx:>5} {d:>10} {closes[h]:>8.2f} {g*100:>5.1f}% {label:>8}")

print(f"\n--- 合并后候选分析 ---")
for idx, (h, l, g) in enumerate(merged):
    d = str(df.iloc[h]['trade_date'])
    
    post_high = closes[h:entry_idx+1]
    if len(post_high) < 5:
        print(f"候选{idx}: {d} 高点{closes[h]:.2f} 涨幅{g*100:.1f}% 回调数据不足")
        continue
    
    low_after_high = post_high.min()
    pullback_pct = (closes[h] - low_after_high) / closes[h]
    low_pos = int(np.argmin(post_high))
    adjust_days = low_pos
    
    # 量比
    vol_base_start = max(0, h - 60)
    base_vol = volumes[vol_base_start:h].mean() if h > 0 else volumes.mean()
    vol_ratio = post_high[:adjust_days+1].mean() / base_vol if base_vol > 0 else 1.0
    
    # 创新低
    wave1_start_idx = max(0, h - 20)
    pre_low_start = max(0, wave1_start_idx - 20)
    if h >= 40:
        pre_low = closes[pre_low_start:wave1_start_idx+1].min()
    else:
        pre_low = closes[0:h+1].min()
    adj_low = closes[h:entry_idx+1].min()
    is_higher_low = adj_low > pre_low
    
    surge_pct = round(g * 100, 1)
    
    print(f"\n候选{idx}: {d} 高点{closes[h]:.2f} 涨幅{g*100:.1f}%")
    print(f"  入场: {target_date} {closes[entry_idx]:.2f}")
    print(f"  回调: {pullback_pct*100:.1f}% (<10%? {'✅' if pullback_pct < 0.10 else '❌'})")
    print(f"  调整: {adjust_days}天 (<=15? {'✅' if adjust_days <= 15 else '❌'})")
    print(f"  量比: {vol_ratio:.2f} (<0.80? {'✅' if vol_ratio < 0.80 else '❌'})")
    print(f"  涨幅: {surge_pct}% (20-60? {'✅' if 20 <= surge_pct < 60 else '❌'})")
    print(f"  更高低点: {'✅' if is_higher_low else '❌'}(adj_low={adj_low:.2f}, pre_low={pre_low:.2f})")
