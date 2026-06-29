import sys
sys.path.insert(0, r'D:\mystock')
import numpy as np
from wave2_pattern_scanner import WavePatternDetector, SIDEWAYS_VOL_MAX, SIDEWAYS_ADJUST_MAX, SCORE_SIDWAYS_MIN

scanner = WavePatternDetector(force_date='20260625')
scanner._scan_data = scanner.load_data('000920.SZ', lookback=500)

df = scanner._scan_data
closes = df['close'].values
highs = df['high'].values
lows = df['low'].values
volumes = df['vol'].values
n = len(df)

wave1_candidates = scanner._find_recent_wave1(closes, n, max_lookback=80)
for h_idx, _, surge_gain in wave1_candidates:
    date = df.iloc[h_idx]['trade_date']
    high_price = highs[h_idx]
    
    post_high_lows = lows[h_idx:]
    low_after_high = post_high_lows.min()
    pullback_pct = (high_price - low_after_high) / high_price
    low_pos = int(np.argmin(post_high_lows))
    adjust_days = low_pos
    entry_idx = h_idx + low_pos
    
    print(f"\n=== 波峰: {date} ===")
    print(f"回调幅度={pullback_pct*100:.1f}%")
    print(f"调整天数={adjust_days}")
    
    ma20_bfq_key = [k for k in ['ma_bfq_20'] if k in df.columns]
    is_ma20_support = False
    if ma20_bfq_key:
        ma20_val = df[ma20_bfq_key[0]].iloc[entry_idx]
        adj_factor = df['adj_factor'].iloc[entry_idx] if 'adj_factor' in df.columns else 1.0
        ma20_bfq_actual = ma20_val * adj_factor
        if low_after_high > ma20_bfq_actual * 0.95:
            is_ma20_support = (0 < pullback_pct < 0.25)
    print(f"is_ma20_support={is_ma20_support}")
    
    vol_base_start = max(0, h_idx - 60)
    base_vol = volumes[vol_base_start:h_idx].mean() if h_idx > 0 else volumes.mean()
    vol_ratio = volumes[h_idx:h_idx+adjust_days+1].mean() / base_vol if base_vol > 0 else 1.0
    
    if vol_ratio >= 3.0:
        print("被量比条件过滤")
        continue
    
    surge_pct = round(surge_gain * 100, 1)
    print(f"surge_pct={surge_pct}")
    
    if is_ma20_support:
        if not (0.02 <= pullback_pct < 0.25 and 20 <= surge_pct < 80):
            print("被硬过滤条件过滤")
            continue
    else:
        if not (0.02 <= pullback_pct < 0.10 and 20 <= surge_pct < 60):
            print("被硬过滤条件过滤")
            continue
    
    print("通过所有过滤条件")
    
    prev_row = df.iloc[entry_idx - 1] if entry_idx > 0 else None
    gap_to_peak = (high_price - closes[entry_idx]) / closes[entry_idx]
    is_long_consolidation = (adjust_days > 30 and
                             closes[entry_idx] > df.iloc[entry_idx].get('ma250', 0) and
                             vol_ratio < 0.7)
    limitup_score, volume_recovery_score = scanner._calc_limitup_features(df, entry_idx)
    
    row_sc = df.iloc[entry_idx]
    row_sc = scanner._fix_volume_ratio(df, entry_idx, row_sc)
    total_mv_b = float(row_sc.get('total_mv', 0)) / 1e8
    atr_pct_sc = float(row_sc.get('atr_qfq', 0)) / float(row_sc.get('close_qfq', row_sc['close'])) if float(row_sc.get('close_qfq', 0)) > 0 else 0.02
    
    score_result = scanner.scorer.score(row_sc, prev_row,
                                      wave1_gain_pct=surge_pct,
                                      new_high_confirmed=False,
                                      new_high_pullback=False,
                                      is_higher_low=True,
                                      pattern_type='强势横盘',
                                      gap_to_peak_pct=gap_to_peak,
                                      pullback_pct=pullback_pct,
                                      is_deep_long_consolidation=is_long_consolidation,
                                      limitup_score=limitup_score,
                                      volume_recovery_score=volume_recovery_score,
                                      atr_pct=atr_pct_sc,
                                      market_cap_b=total_mv_b)
    
    print(f"评分={score_result['total']}")
    
    divs = scanner.scorer.check_divergence(df, entry_idx)
    for key, div in divs.items():
        if div.get('found'):
            score_result['total'] += div['pts']
            print(f"底背离加分: {div['pts']}")
    
    dmi_cross = scanner.scorer.check_dmi_crossover(df, entry_idx)
    if dmi_cross.get('found'):
        score_result['total'] += dmi_cross['pts']
        print(f"DMI交叉加分: {dmi_cross['pts']}")
    
    bonus_pts, bonus_desc = scanner._board_bonus('000920.SZ', '强势横盘')
    print(f"板块加分: {bonus_pts} ({bonus_desc})")
    score_result['total'] += bonus_pts
    
    print(f"最终评分={score_result['total']}")
    print(f"SCORE_SIDWAYS_MIN={SCORE_SIDWAYS_MIN}")
    
    if score_result['total'] >= SCORE_SIDWAYS_MIN:
        print("✅ 检测到信号！")
    else:
        print("❌ 评分不足")