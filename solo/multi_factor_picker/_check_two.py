# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

import pandas as pd
import numpy as np
import wave2_pattern_scanner as scanner

detector = scanner.WavePatternDetector()

stocks = [
    ('600458.SH', '时代新材'),
    ('002741.SZ', '光华科技'),
]

for code, name in stocks:
    print(f"\n{'='*60}")
    print(f"{name} ({code})")
    print('='*60)
    
    df = detector.load_data(code, lookback=500)
    if df is None or len(df) < 60:
        print(f"  ❌ 数据不足")
        continue
    
    print(f"  数据范围: {df.iloc[0]['trade_date']} ~ {df.iloc[-1]['trade_date']}")
    print(f"  最新收盘: {df.iloc[-1]['close']:.2f}")
    
    closes = df['close'].values
    n = len(df)
    
    wave1_candidates = detector._find_recent_wave1(closes, n)
    found_signal = False
    
    for wave1_high_idx, _, surge_gain in wave1_candidates[:3]:
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
        
        volumes = df['vol'].values
        vol_base_start = max(0, wave1_high_idx - 60)
        base_vol = volumes[vol_base_start:wave1_high_idx].mean() if wave1_high_idx > 0 else volumes.mean()
        vol_ratio = post_high[:adjust_days + 1].mean() / base_vol if base_vol > 0 else 1.0
        
        if vol_ratio >= 0.80:
            continue
        
        entry_idx = wave1_high_idx + low_pos
        if entry_idx >= n:
            continue
        
        surge_pct = round(surge_gain * 100, 1)
        # v3.4条件
        if not (0.02 <= pullback_pct < 0.10 and 20 <= surge_pct < 60):
            continue
        
        wave1_start_idx = max(0, wave1_high_idx - 20)
        pre_low_start = max(0, wave1_start_idx - 20)
        if wave1_high_idx >= 40:
            pre_low = closes[pre_low_start:wave1_start_idx+1].min()
        else:
            pre_low = closes[0:wave1_high_idx+1].min()
        adj_low = closes[wave1_high_idx:entry_idx+1].min()
        is_higher_low = adj_low > pre_low
        if not is_higher_low:
            continue
        
        entry_date = df.iloc[entry_idx]['trade_date']
        prev_row = df.iloc[entry_idx - 1] if entry_idx > 0 else None
        new_high_confirmed = False
        post_high_all = closes[wave1_high_idx:entry_idx + 1]
        if len(post_high_all) > 1 and post_high_all.max() > wave1_high_price:
            new_high_confirmed = True
        
        gap_to_peak = (wave1_high_price - closes[entry_idx]) / closes[entry_idx]
        score_result = detector.scorer.score(
            df.iloc[entry_idx], prev_row,
            wave1_gain_pct=surge_pct,
            new_high_confirmed=new_high_confirmed,
            new_high_pullback=False,
            is_higher_low=is_higher_low,
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
        
        bonus_pts, _ = detector._board_bonus(code, '强势横盘')
        score_result['total'] += bonus_pts
        
        found_signal = True
        status = "✅ 有信号" if score_result['total'] >= 20 else "❌ 无信号"
        print(f"\n  信号日期: {entry_date}")
        print(f"  一波涨幅: {surge_pct}% (需20-60%)")
        print(f"  回调深度: {pullback_pct*100:.1f}% (需2-10%)")
        print(f"  评分: {score_result['total']}分 {status}")
    
    if not found_signal:
        print(f"\n  ❌ v3.4条件下无信号")
