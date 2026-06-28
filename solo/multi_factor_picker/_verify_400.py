# -*- coding: utf-8 -*-
"""
验证：用前400只合格股跑6月数据，确认每日信号分布
"""
import sys, os
sys.path.insert(0, r'D:\mystock\solo')
sys.path.insert(0, r'D:\mystock')

import pandas as pd
import numpy as np
from collections import Counter

pool_df = pd.read_csv(r'd:\mystock\solo\report_daily\bull_stocks_qualified.csv')
all_codes = pool_df['code'].tolist()
codes = []
for c in all_codes[:400]:
    c = str(c).zfill(6)
    if c.startswith(('60', '688')):
        codes.append(c + '.SH')
    else:
        codes.append(c + '.SZ')

print(f"测试股票数: {len(codes)}只（前400只合格股）")

import wave2_pattern_scanner as scanner

detector = scanner.WavePatternDetector()

all_signals = []

for i, code in enumerate(codes):
    if i % 100 == 0:
        print(f"  进度: {i}/{len(codes)}")
    try:
        df = detector.load_data(code, lookback=500)
        if df is None or len(df) < 60:
            continue
        
        closes = df['close'].values
        volumes = df['vol'].values
        n = len(df)
        
        wave1_candidates = detector._find_recent_wave1(closes, n)
        for wave1_high_idx, _, surge_gain in wave1_candidates:
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
            
            surge_pct = round(surge_gain * 100, 1)
            if not (0.03 <= pullback_pct < 0.10 and 20 <= surge_pct < 60):
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
            new_high_pullback = False
            post_high_all = closes[wave1_high_idx:entry_idx + 1]
            if len(post_high_all) > 1:
                max_post = post_high_all.max()
                if max_post > wave1_high_price:
                    new_high_confirmed = True
                    new_high_idx_local = np.argmax(post_high_all)
                    if new_high_idx_local < len(post_high_all) - 1:
                        new_high_pullback = True
            
            gap_to_peak = (wave1_high_price - closes[entry_idx]) / closes[entry_idx]
            score_result = detector.scorer.score(
                df.iloc[entry_idx], prev_row,
                wave1_gain_pct=surge_pct,
                new_high_confirmed=new_high_confirmed,
                new_high_pullback=new_high_pullback,
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
            
            if score_result['total'] >= 25:
                trade_date = str(df.iloc[entry_idx]['trade_date'])
                if trade_date.startswith('202606'):
                    all_signals.append({
                        'date': trade_date,
                        'code': code,
                        'score': score_result['total'],
                    })
    except Exception as e:
        continue

print(f"\n6月信号总数: {len(all_signals)}个")
date_counts = Counter(s['date'] for s in all_signals)
sorted_dates = sorted(date_counts.keys())
print(f"6月有信号天数: {len(sorted_dates)}天")
print(f"\n每日分布:")
for d in sorted_dates:
    print(f"  {d}: {date_counts[d]}只")

# 推算全量
factor = 946 / 400
print(f"\n推算全量946只:")
print(f"  6月总信号约: {len(all_signals) * factor:.0f}只")
print(f"  日均约: {len(all_signals) * factor / 22:.2f}只")

# 估算有信号天数（用实际日期分布外推）
# 假设新增的信号在现有有信号天的基础上，每天增加比例相同
# 实际上可能更复杂，但这是保守估计
est_daily_avg = len(all_signals) * factor / 22
total_signals = est_daily_avg * 22
est_days = 22 * (1 - (21/22) ** total_signals)
print(f"  估算有信号天数约: {est_days:.0f}/22天 ({est_days/22*100:.0f}%)")
