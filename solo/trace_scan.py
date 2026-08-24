# -*- coding: utf-8 -*-
"""Trace the _scan_for_impulse method."""
import numpy as np
import pandas as pd
from test_rib_v2 import create_rib_pattern
from rib.indicators import enrich, find_local_extremes


def main():
    df = enrich(create_rib_pattern())
    end_idx = len(df) - 1
    highs = df["high"].values.astype(float)
    lows = df["low"].values.astype(float)
    vols = df["vol"].values.astype(float)

    print(f"Data: {len(df)} rows, end_idx={end_idx}")

    scan_start = max(60, end_idx - 150)
    print(f"Scan: [{scan_start}, {end_idx}]")

    # Strategy 1: Lowest point
    seg_lows = lows[scan_start:end_idx + 1]
    min_idx_in_seg = int(np.argmin(seg_lows))
    lowest_idx = scan_start + min_idx_in_seg
    lowest_price = lows[lowest_idx]
    print(f"Lowest: idx={lowest_idx}, price={lowest_price:.2f}")

    # Check what's from that low
    seg_highs = highs[lowest_idx:end_idx + 1]
    high_offset = int(np.argmax(seg_highs))
    high_idx = lowest_idx + high_offset
    high_price = seg_highs.max()
    ret = (high_price - lowest_price) / lowest_price
    days = high_idx - lowest_idx
    print(f"  Max high: idx={high_idx}, price={high_price:.2f}, ret={ret*100:.1f}%, days={days}")

    if not (ret >= 0.15 and 3 <= days <= 60):
        print("  FAILS filter!")

    # Strategy 2: Local lows
    _, low_indices = find_local_extremes(lows[scan_start:end_idx+1], order=5)
    low_indices = low_indices + scan_start
    print(f"\nLocal lows (order=5): {len(low_indices)}")

    # Filter: min_days_from_end = 20
    filtered = [li for li in low_indices if (end_idx - li) >= 20]
    print(f"Filtered (age >= 20): {len(filtered)}")

    for li in filtered:
        low_price = lows[li]
        seg_highs = highs[li:end_idx + 1]
        hi = li + int(np.argmax(seg_highs))
        hp = seg_highs.max()
        r = (hp - low_price) / low_price
        d = hi - li
        print(f"  low={li}({low_price:.2f}), high={hi}({hp:.2f}), ret={r*100:.1f}%, days={d}")
        if r < 0.15 * 0.8:
            print(f"    SKIP: ret too low")
            continue
        if d < 3 or d > 80:
            print(f"    SKIP: days out of range")
            continue

        # Check vol
        vol_ma20 = df["vol_ma20"].values
        avg_vol = np.mean(vols[li:hi + 1])
        baseline = float(vol_ma20[li]) if not np.isnan(vol_ma20[li]) else np.mean(vols[max(0, li-20):li])
        vr = avg_vol / baseline if baseline > 0 else 0
        print(f"    vol_ratio={vr:.2f}, avg={avg_vol:.0f}, baseline={baseline:.0f}")
        if vr < 0.8:
            print(f"    SKIP: volume too low")

    print("\n--- Problem analysis ---")
    print(f"Lowest idx={lowest_idx} gives days={days}, which is {'OK' if 3<=days<=60 else 'TOO LARGE'}")
    print(f"Check: the impulse phase is around idx 94-117, which is {end_idx - 94} days from end")
    print(f"  The days from low(94) to max high is {high_idx - 94} days")


if __name__ == '__main__':
    main()
