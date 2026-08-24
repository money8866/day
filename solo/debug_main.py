# -*- coding: utf-8 -*-
"""Debug the main impulse detection path."""
import numpy as np
from test_rib_v2 import create_rib_pattern
from rib.indicators import enrich, find_local_extremes

df = enrich(create_rib_pattern())
highs = df["high"].values.astype(float)
lows = df["low"].values.astype(float)
end_idx = len(df) - 1

lowest_idx = 94
lowest_price = lows[lowest_idx]
search_limit = min(lowest_idx + 60, end_idx)
min_return = 0.15
local_high_order = 8

seg_highs = highs[lowest_idx:search_limit + 1]
local_high_offsets, _ = find_local_extremes(seg_highs, order=local_high_order)
print(f"local_high_offsets (order={local_high_order}): {list(local_high_offsets)}")

peak_found = False
for offset in local_high_offsets:
    idx = lowest_idx + int(offset)
    price = highs[idx]
    days = idx - lowest_idx
    ret_from_low = (price - lowest_price) / lowest_price
    
    print(f"\nChecking offset={offset}, idx={idx}, price={price:.2f}, days={days}, ret={ret_from_low*100:.1f}%")
    
    if days < 3:
        print("  SKIP: days < 3")
        continue
    if ret_from_low < min_return:
        print("  SKIP: ret < min_return")
        continue
    
    post_end = min(idx + 15, search_limit + 1)
    post_highs = highs[idx:post_end]
    post_max = post_highs.max()
    threshold = price * 1.01
    print(f"  post_highs range: [{idx}, {post_end}), max={post_max:.2f}, threshold={threshold:.2f}")
    
    if len(post_highs) > 1 and post_max <= threshold:
        print(f"  >>> PEAK FOUND at idx={idx}, price={price:.2f}")
        peak_found = True
        break
    else:
        print(f"  NO: {post_max:.2f} > {threshold:.2f}")

if not peak_found:
    print("\nNo peak found in loop, using simple max")
    seg = highs[lowest_idx:search_limit + 1]
    peak_offset = int(np.argmax(seg))
    print(f"  Simple max: idx={lowest_idx+peak_offset}, price={seg.max():.2f}")
