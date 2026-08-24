# -*- coding: utf-8 -*-
"""Detailed trace of the impulse detection."""
import numpy as np
from test_rib_v2 import create_rib_pattern
from rib.indicators import enrich
from rib.engine import RIBEngine, RIBResult

df = enrich(create_rib_pattern())
engine = RIBEngine()
end_idx = len(df) - 1

# Trace the impulse detection
highs = df["high"].values.astype(float)
lows = df["low"].values.astype(float)

min_return = 0.15
lowest_idx = 94
lowest_price = lows[lowest_idx]

print(f"Lowest: idx={lowest_idx}, price={lowest_price}")

# Trace the retracement detection
search_limit = min(lowest_idx + 60, end_idx)
highest_seen = lowest_price
highest_idx = lowest_idx
peak_found = False

for i in range(lowest_idx + 1, search_limit + 1):
    if highs[i] > highest_seen:
        highest_seen = highs[i]
        highest_idx = i
    if highest_seen > lowest_price * (1 + min_return):
        current_retrace = (highest_seen - lows[i]) / highest_seen
        if i <= 105 or current_retrace >= 0.05:
            print(f"  i={i:3d} high={highs[i]:.2f} low={lows[i]:.2f} peak={highest_seen:.2f} ret={current_retrace*100:.1f}% days_from_peak={i-highest_idx}")
        if current_retrace >= 0.05 and highest_idx > lowest_idx + 3:
            peak_found = True
            print(f"  >>> PEAK FOUND at idx={highest_idx}, price={highest_seen:.2f}, ret={(highest_seen-lowest_price)/lowest_price*100:.1f}%")
            break

if not peak_found:
    print(f"  NO PEAK FOUND, using max: idx={highest_idx}, price={highest_seen:.2f}")

# Now let's also find the "true" impulse high: max in days 100-118
true_impulse_start = 100
true_impulse_end = 117
true_highs = highs[true_impulse_start:true_impulse_end+1]
true_peak_off = int(np.argmax(true_highs))
true_peak_idx = true_impulse_start + true_peak_off
true_peak = highs[true_peak_idx]
true_ret = (true_peak - lows[true_impulse_start]) / lows[true_impulse_start]
print(f"\nTrue impulse: idx={true_impulse_start} to {true_impulse_end}")
print(f"True peak: idx={true_peak_idx}, price={true_peak:.2f}, ret={true_ret*100:.1f}%")
print(f"True low: idx={true_impulse_start}, price={lows[true_impulse_start]:.2f}")
