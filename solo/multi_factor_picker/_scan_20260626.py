# -*- coding: utf-8 -*-
"""
扫描20260626的强势横盘信号（带去重）
"""
import sys, os
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

import pandas as pd
import numpy as np

pool_df = pd.read_csv(r'd:\mystock\solo\report_daily\bull_stocks_qualified.csv')
all_codes = pool_df['code'].tolist()

import wave2_pattern_scanner as scanner

detector = scanner.WavePatternDetector()

target_date = '20260626'
# 去重字典：{code: {entry_date: best_result}}
seen_signals = {}

for i, c in enumerate(all_codes):
    c = str(c).zfill(6)
    if c.startswith(('60', '688')):
        code = c + '.SH'
    else:
        code = c + '.SZ'
    
    if i % 200 == 0:
        print(f"  进度: {i}/{len(all_codes)}")
    
    try:
        df = detector.load_data(code, lookback=500)
        if df is None or len(df) < 60:
            continue
        
        closes = df['close'].values
        volumes = df['vol'].values
        n = len(df)
        
        wave1_candidates = detector._find_recent_wave1(closes, n)
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
            
            vol_base_start = max(0, wave1_high_idx - 60)
            base_vol = volumes[vol_base_start:wave1_high_idx].mean() if wave1_high_idx > 0 else volumes.mean()
            vol_ratio = post_high[:adjust_days + 1].mean() / base_vol if base_vol > 0 else 1.0
            
            if vol_ratio >= 0.80:
                continue
            
            entry_idx = wave1_high_idx + low_pos
            if entry_idx >= n:
                continue
            
            entry_date = str(df.iloc[entry_idx]['trade_date'])
            if entry_date != target_date:
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
            
            if score_result['total'] >= 20:
                name = pool_df[pool_df['code'] == int(c[:6])]['name'].values[0] if len(pool_df[pool_df['code'] == int(c[:6])]) > 0 else c
                signal = {
                    'code': code,
                    'name': name,
                    'score': score_result['total'],
                    'pullback': round(pullback_pct * 100, 1),
                    'surge': surge_pct,
                    'entry_price': df.iloc[entry_idx]['close'],
                    'details': score_result['details']
                }
                # 去重：同一天同一只股票只保留评分最高的
                key = (code, entry_date)
                if key not in seen_signals or signal['score'] > seen_signals[key]['score']:
                    seen_signals[key] = signal
    except Exception as e:
        continue

# 合并结果
signals = list(seen_signals.values())
signals = sorted(signals, key=lambda x: -x['score'])

print(f"\n{'='*70}")
print(f"{target_date} 强势横盘信号 (v3.5: 一波取最高点 + 评分≥20 + 回调2-10% + 一波20-60%)")
print(f"{'='*70}")
print(f"共 {len(signals)} 只（去重后）")

if signals:
    for i, s in enumerate(signals, 1):
        print(f"\n【第{i}名】{s['name']} ({s['code']})")
        print(f"  评分: {s['score']}分")
        print(f"  入场价: {s['entry_price']:.2f}")
        print(f"  回调: {s['pullback']}% | 一波: {s['surge']}%")
        print(f"  加分项: {' + '.join(s['details'][:4])}")
else:
    print("\n当天无信号")
