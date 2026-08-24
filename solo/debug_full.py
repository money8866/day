# -*- coding: utf-8 -*-
"""Full debug of the pipeline."""
import numpy as np
from test_rib_v2 import create_rib_pattern
from rib.indicators import enrich
from rib.engine import RIBEngine

df = enrich(create_rib_pattern())
engine = RIBEngine()
end_idx = len(df) - 1

# Get impulse
imp = engine._scan_for_impulse(df, end_idx)
if imp:
    print(f"IMPULSE: low_idx={imp.impulse_low_idx} (price={imp.impulse_low:.2f}), high_idx={imp.impulse_high_idx} (price={imp.impulse_high:.2f})")
    print(f"  ret={imp.impulse_return*100:.1f}%, days={imp.impulse_days}, vol={imp.volume_ratio:.2f}")
else:
    print("NO IMPULSE")
    exit(1)

# Get base
base = engine._detect_post_impulse_base(df, imp, end_idx)
if base:
    print(f"BASE: start_idx={base.base_start_idx} (price={base.base_start_price:.2f}), end_idx={base.base_end_idx} (price={base.base_end_price:.2f})")
    print(f"  high={base.base_high:.2f}, low={base.base_low:.2f}, days={base.base_days}")
    print(f"  retain={base.impulse_retain_ratio*100:.1f}%, shrink={base.volume_shrink:.2f}")
else:
    print("NO BASE")
    exit(1)

# Get breakout
bo = engine._detect_second_leg_breakout(df, imp, base, end_idx)
if bo:
    print(f"BREAKOUT: idx={bo.breakout_idx}, price={bo.breakout_price:.2f}")
    print(f"  imp_high={imp.impulse_high:.2f}, distance_atr={bo.breakout_distance_atr:.2f}")
    print(f"  vol={bo.volume_ratio:.2f}, close_loc={bo.close_location:.2f}")
    print(f"  is_fake={bo.is_fake_breakout}")
else:
    print("NO BREAKOUT")

# Print the actual highs around the breakout zone
print("\n--- Price table around breakout zone ---")
highs = df["high"].values
lows = df["low"].values
closes = df["close"].values
vols = df["vol"].values
for i in range(135, 155):
    atr_val = float(df["atr20"].values[i]) if "atr20" in df.columns and not np.isnan(df["atr20"].values[i]) else 0
    print(f"  idx={i:3d} close={closes[i]:.2f} high={highs[i]:.2f} low={lows[i]:.2f} vol={vols[i]:.0f} atr={atr_val:.2f}")
