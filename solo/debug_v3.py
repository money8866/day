# -*- coding: utf-8 -*-
"""Detailed debug of v3 test."""
import numpy as np
from test_rib_v3 import create_rib_pattern_v3
from rib.indicators import enrich
from rib.engine import RIBEngine

df = enrich(create_rib_pattern_v3())
engine = RIBEngine()
end_idx = len(df) - 1

# Get impulse
imp = engine._scan_for_impulse(df, end_idx)
if imp:
    print(f"IMPULSE: low_idx={imp.impulse_low_idx} (price={imp.impulse_low:.2f})")
    print(f"  high_idx={imp.impulse_high_idx} (price={imp.impulse_high:.2f})")
    print(f"  ret={imp.impulse_return*100:.1f}%, days={imp.impulse_days}")
    print(f"  volume_ratio={imp.volume_ratio:.2f}")
    print(f"  confirmed={imp.is_reversal_confirmed}")
else:
    print("NO IMPULSE")
    exit(1)

# Get peak
peak = engine.peak_detector.detect(df, imp, end_idx)
if peak.is_peak_valid:
    print(f"\nPEAK: idx={peak.peak_idx}, price={peak.peak_price:.2f}")
else:
    print(f"\nPEAK: NOT VALID")

# Get base
base = engine.base_detector.detect(df, imp, peak, end_idx)
if base.is_base:
    print(f"\nBASE: start={base.platform_start_idx} (price={base.start_price:.2f}), end={base.platform_end_idx} (price={base.end_price:.2f})")
    print(f"  days={base.platform_days}, high={base.base_high:.2f}, low={base.base_low:.2f}")
    print(f"  retain={base.retain_ratio*100:.1f}%, vol_shrink={base.volume_shrink_ratio:.2f}")
    print(f"  ma20_slope={base.ma20_slope:.4f}")
else:
    print(f"\nBASE: NOT VALID")
    exit(1)

# Get breakout
bo = engine.breakout_detector.detect(df, imp, base, end_idx)
print(f"\nBREAKOUT: is_breakout={bo.is_breakout}")
if bo.is_breakout:
    print(f"  idx={bo.breakout_idx}, price={bo.breakout_price:.2f}")
    print(f"  imp_high={imp.impulse_high:.2f}, dist_atr={bo.breakout_distance_atr:.2f}")
    print(f"  vol={bo.volume_ratio:.2f}, close_loc={bo.close_location:.2f}")
    print(f"  is_fake={bo.is_fake_breakout}")
else:
    print("  No breakout found")
    # Debug: what are the search parameters?
    search_start = base.platform_end_idx + 1
    print(f"  Search range: [{search_start}, {end_idx}]")
    print(f"  Impulse high: {imp.impulse_high:.2f}")
    print(f"  Breakout threshold: {imp.impulse_high + 0.3 * df['atr20'].values[end_idx]:.2f}")
    
    # Print prices in search range
    highs = df["high"].values
    closes = df["close"].values
    vols = df["vol"].values
    atrs = df["atr20"].values
    print("\n  Bars in search range:")
    for i in range(search_start, min(search_start + 10, end_idx + 1)):
        atr_val = float(atrs[i]) if not np.isnan(atrs[i]) else 0
        threshold = imp.impulse_high + 0.3 * atr_val
        vol_ma = float(df["vol_ma20"].values[i]) if "vol_ma20" in df.columns and not np.isnan(df["vol_ma20"].values[i]) else 0
        vol_ratio = vols[i] / vol_ma if vol_ma > 0 else 0
        day_range = highs[i] - float(df["low"].values[i])
        close_loc = (closes[i] - float(df["low"].values[i])) / day_range if day_range > 0 else 0.5
        print(f"    idx={i:3d} close={closes[i]:.2f} high={highs[i]:.2f} vol={vols[i]:.0f} vr={vol_ratio:.2f} atr={atr_val:.2f} thresh={threshold:.2f} close>thresh={closes[i]>threshold} close_loc={close_loc:.2f}")
