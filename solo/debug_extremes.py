# -*- coding: utf-8 -*-
"""Debug find_local_extremes for impulse detection."""
import numpy as np
from test_rib_v2 import create_rib_pattern
from rib.indicators import enrich, find_local_extremes

df = enrich(create_rib_pattern())
highs = df["high"].values.astype(float)
lows = df["low"].values.astype(float)

lowest_idx = 94
search_limit = min(lowest_idx + 60, len(df) - 1)
seg_highs = highs[lowest_idx:search_limit + 1]

for order in [3, 5, 8, 10, 15]:
    offsets, _ = find_local_extremes(seg_highs, order=order)
    indices = [lowest_idx + o for o in offsets]
    prices = [highs[i] for i in indices]
    days = [i - lowest_idx for i in indices]
    rets = [(highs[i] - lows[lowest_idx]) / lows[lowest_idx] * 100 for i in indices]
    print(f"order={order}: {len(offsets)} peaks")
    for i, p, d, r in zip(indices, prices, days, rets):
        print(f"  idx={i:3d} price={p:.2f} days={d:2d} ret={r:.1f}%")
