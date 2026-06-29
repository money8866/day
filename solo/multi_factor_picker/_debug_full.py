import sys
sys.path.insert(0, r'D:\mystock')
import numpy as np
from wave2_pattern_scanner import WavePatternDetector, SIDEWAYS_VOL_MAX, SIDEWAYS_ADJUST_MAX

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
        print(f"MA20={ma20_bfq_actual:.2f}, low={low_after_high:.2f}")
        if low_after_high > ma20_bfq_actual * 0.95:
            is_ma20_support = (0 < pullback_pct < 0.25)
    print(f"is_ma20_support={is_ma20_support}")
    
    vol_base_start = max(0, h_idx - 60)
    base_vol = volumes[vol_base_start:h_idx].mean() if h_idx > 0 else volumes.mean()
    vol_ratio = volumes[h_idx:h_idx+adjust_days+1].mean() / base_vol if base_vol > 0 else 1.0
    print(f"量比={vol_ratio:.2f}")
    
    if vol_ratio >= 3.0:
        print("被量比条件过滤")
        continue
    
    print("通过量比过滤")
    
    if entry_idx >= n:
        print("entry_idx越界")
        continue
    
    print(f"entry_idx={entry_idx}, n={n}")
    
    prev_row = df.iloc[entry_idx - 1] if entry_idx > 0 else None
    print(f"prev_row存在: {prev_row is not None}")
    
    gap_to_peak = (high_price - closes[entry_idx]) / closes[entry_idx]
    print(f"gap_to_peak={gap_to_peak*100:.1f}%")
    
    row_sc = df.iloc[entry_idx]
    row_sc = scanner._fix_volume_ratio(df, entry_idx, row_sc)
    
    score_result = scanner.scorer.score(row_sc, prev_row,
                                      wave1_gain_pct=round(surge_gain * 100, 1),
                                      new_high_confirmed=False,
                                      new_high_pullback=False,
                                      is_higher_low=True,
                                      pattern_type='强势横盘',
                                      gap_to_peak_pct=gap_to_peak,
                                      pullback_pct=pullback_pct,
                                      is_deep_long_consolidation=False,
                                      limitup_score=0,
                                      volume_recovery_score=0,
                                      atr_pct=0.02,
                                      market_cap_b=0)
    
    print(f"评分={score_result['total']}")
    print(f"评分详情={score_result['details']}")
    
    wave1_start_idx = max(0, h_idx - 20)
    pre_low_start = max(0, wave1_start_idx - 20)
    if h_idx >= 40:
        pre_low = closes[pre_low_start:wave1_start_idx+1].min()
    else:
        pre_low = closes[0:h_idx+1].min()
    adj_low = closes[h_idx:entry_idx+1].min()
    is_higher_low = adj_low > pre_low
    print(f"\n创新低检测:")
    print(f"pre_low={pre_low:.2f}, adj_low={adj_low:.2f}")
    print(f"is_higher_low={is_higher_low}")
    
    recent_window = closes[max(0, entry_idx - 9):entry_idx + 1]
    recent_high = recent_window.max()
    print(f"\n距近日高点检查:")
    print(f"recent_high={recent_high:.2f}, close={closes[entry_idx]:.2f}")
    print(f"close < recent_high * 0.85: {closes[entry_idx] < recent_high * 0.85}")
    
    high_30d = closes[-30:].max()
    high_20d = closes[-20:].max()
    print(f"\n高点下降检测:")
    print(f"high_30d={high_30d:.2f}, high_20d={high_20d:.2f}")
    print(f"high_20d < high_30d * 0.95: {high_20d < high_30d * 0.95}")