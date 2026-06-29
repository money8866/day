import sys
sys.path.insert(0, r'D:\mystock')
import numpy as np
from wave2_pattern_scanner import WavePatternDetector, SIDEWAYS_VOL_MAX

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
    
    print(f"波峰: {date}")
    print(f"回调幅度={pullback_pct*100:.1f}%")
    print(f"调整天数={adjust_days}")
    
    vol_base_start = max(0, h_idx - 60)
    base_vol = volumes[vol_base_start:h_idx].mean() if h_idx > 0 else volumes.mean()
    vol_ratio = volumes[h_idx:h_idx+adjust_days+1].mean() / base_vol if base_vol > 0 else 1.0
    print(f"量比={vol_ratio:.2f}")
    print(f"SIDEWAYS_VOL_MAX={SIDEWAYS_VOL_MAX}")
    print(f"量比 >= SIDEWAYS_VOL_MAX: {vol_ratio >= SIDEWAYS_VOL_MAX}")