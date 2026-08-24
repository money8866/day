# -*- coding: utf-8 -*-
"""Debug the candidate selection."""
import numpy as np
from test_rib_v2 import create_rib_pattern
from rib.indicators import enrich, find_local_extremes
from rib.engine import RIBEngine, ImpulseResult

df = enrich(create_rib_pattern())
engine = RIBEngine()
end_idx = len(df) - 1

highs = df["high"].values.astype(float)
lows = df["low"].values.astype(float)
vols = df["vol"].values.astype(float)

min_return = 0.15
scan_lookback = 200
scan_start = max(60, end_idx - scan_lookback)

# Find the absolute low
seg_lows = lows[scan_start:end_idx + 1]
min_idx_in_seg = int(np.argmin(seg_lows))
lowest_idx = scan_start + min_idx_in_seg
lowest_price = lows[lowest_idx]
print(f"Absolute low: idx={lowest_idx}, price={lowest_price:.2f}")

# Find local lows
_, low_indices = find_local_extremes(lows[scan_start:end_idx+1], order=5)
low_indices = low_indices + scan_start
print(f"Local lows (order=5): {list(zip(low_indices, [f'{lows[x]:.2f}' for x in low_indices]))}")

# Also try order=3
_, low_indices3 = find_local_extremes(lows[scan_start:end_idx+1], order=3)
low_indices3 = low_indices3 + scan_start
print(f"Local lows (order=3): {list(zip(low_indices3, [f'{lows[x]:.2f}' for x in low_indices3]))}")

# Now let's trace what candidates would be found
print("\n--- Main scan (from absolute low) ---")
search_limit = min(lowest_idx + 60, end_idx)
# Quick check: what highs are in [94, 154]?
seg = highs[lowest_idx:search_limit + 1]
peak_off = int(np.argmax(seg))
print(f"Max high in [{lowest_idx}, {search_limit}]: idx={lowest_idx+peak_off}, price={seg.max():.2f}")

# Try the windowed detection
confirmed_peak_price = lowest_price
confirmed_peak_idx = lowest_idx
for i in range(lowest_idx + 1, search_limit + 1):
    if highs[i] > confirmed_peak_price:
        confirmed_peak_price = highs[i]
        confirmed_peak_idx = i
    if confirmed_peak_idx is not None and i - confirmed_peak_idx >= 2:
        w_low = lows[confirmed_peak_idx:min(i+1, search_limit+1)].min()
        retrace = (confirmed_peak_price - w_low) / confirmed_peak_price
        if retrace >= 0.08:
            no_new = highs[confirmed_peak_idx:min(i+1, search_limit+1)].max() <= confirmed_peak_price * 1.01
            if no_new and confirmed_peak_price > lowest_price * (1 + min_return):
                print(f"  PEAK CONFIRMED at idx={confirmed_peak_idx}, price={confirmed_peak_price:.2f}, ret={confirmed_peak_price/lowest_price-1:.1%}")
                break
            else:
                print(f"  i={i}: peak={confirmed_peak_idx}, retrace={retrace:.1%}, no_new={no_new}, above_min_ret={confirmed_peak_price > lowest_price * (1 + min_return)}")

print(f"  Final peak: idx={confirmed_peak_idx}, price={confirmed_peak_price:.2f}, days={confirmed_peak_idx-lowest_idx}, ret={(confirmed_peak_price-lowest_price)/lowest_price*100:.1f}%")

print("\n--- Alternate lows ---")
for li in low_indices:
    if li >= lowest_idx or (end_idx - li) < 20:
        continue
    low_p = lows[li]
    local_search = min(li + 60, end_idx)
    seg2 = highs[li:local_search + 1]
    peak_off2 = int(np.argmax(seg2))
    print(f"  Low idx={li}, price={low_p:.2f} -> max high at {li+peak_off2}, price={seg2.max():.2f}, ret={(seg2.max()-low_p)/low_p*100:.1f}%")
