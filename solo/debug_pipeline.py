# -*- coding: utf-8 -*-
"""Debug the full pipeline output more carefully."""
import numpy as np
from test_rib_v2 import create_rib_pattern
from rib.indicators import enrich
from rib.engine import RIBEngine

df = enrich(create_rib_pattern())
engine = RIBEngine()
end_idx = len(df) - 1

# Get the impulse result first
imp = engine._scan_for_impulse(df, end_idx)
if imp:
    print(f"IMPULSE FOUND:")
    print(f"  impulse_low_idx={imp.impulse_low_idx}, price={imp.impulse_low:.2f}")
    print(f"  impulse_high_idx={imp.impulse_high_idx}, price={imp.impulse_high:.2f}")
    print(f"  impulse_return={imp.impulse_return*100:.1f}%")
    print(f"  impulse_days={imp.impulse_days}")
    print(f"  volume_ratio={imp.volume_ratio:.2f}")
    print(f"  is_reversal_confirmed={imp.is_reversal_confirmed}")
    print(f"  broke_ma20={imp.broke_ma20}, broke_ma60={imp.broke_ma60}, broke_trend={imp.broke_trend}")
else:
    print("NO IMPULSE FOUND")
    exit(1)

# Now find the base
base = engine._detect_post_impulse_base(df, imp, end_idx)
if base:
    print(f"\nBASE FOUND:")
    print(f"  base_start_idx={base.base_start_idx}, price={base.base_start_price:.2f}")
    print(f"  base_end_idx={base.base_end_idx}, price={base.base_end_price:.2f}")
    print(f"  base_high={base.base_high:.2f}, base_low={base.base_low:.2f}")
    print(f"  base_days={base.base_days}")
    print(f"  pullback_depth={base.pullback_depth*100:.1f}%")
    print(f"  impulse_retain_ratio={base.impulse_retain_ratio*100:.1f}%")
    print(f"  volume_shrink={base.volume_shrink:.2f}")
    print(f"  ma20_slope={base.ma20_slope:.4f}")
else:
    print("\nNO BASE FOUND")
    exit(1)

# Find breakout
bo = engine._detect_second_leg_breakout(df, imp, base, end_idx)
if bo:
    print(f"\nBREAKOUT FOUND:")
    print(f"  breakout_idx={bo.breakout_idx}, price={bo.breakout_price:.2f}")
    print(f"  volume_ratio={bo.volume_ratio:.2f}")
    print(f"  close_location={bo.close_location:.2f}")
    print(f"  atr_ratio={bo.breakout_atr_ratio:.2f}")
    print(f"  is_fake_breakout={bo.is_fake_breakout}")
else:
    print("\nNO BREAKOUT FOUND")
