# -*- coding: utf-8 -*-
"""Trace the _scan_for_impulse method in detail."""
import numpy as np
from test_rib_v2 import create_rib_pattern
from rib.indicators import enrich, find_local_extremes
from rib.engine import RIBEngine


def main():
    df = enrich(create_rib_pattern())
    end_idx = len(df) - 1
    highs = df["high"].values.astype(float)
    lows = df["low"].values.astype(float)
    vols = df["vol"].values.astype(float)

    print(f"Data: {len(df)} rows, end_idx={end_idx}")

    scan_start = max(60, end_idx - 150)

    # Find lowest
    seg_lows = lows[scan_start:end_idx + 1]
    min_idx_in_seg = int(np.argmin(seg_lows))
    lowest_idx = scan_start + min_idx_in_seg
    lowest_price = lows[lowest_idx]
    print(f"Lowest: idx={lowest_idx}, price={lowest_price:.2f}")

    search_end = min(lowest_idx + 60, end_idx)
    print(f"Search end: {search_end}")

    seg_highs = highs[lowest_idx:search_end + 1]
    local_high_idx, _ = find_local_extremes(seg_highs, order=5)
    local_high_idx = local_high_idx + lowest_idx
    print(f"Local highs (order=5): {len(local_high_idx)}")
    for h in local_high_idx:
        peak_p = highs[h]
        ret = (peak_p - lowest_price) / lowest_price
        days = h - lowest_idx
        print(f"  idx={h}, price={peak_p:.2f}, ret={ret*100:.1f}%, days={days}")

    if len(local_high_idx) == 0:
        print("No local highs found, using simple max")
        search_end_simple = min(lowest_idx + 30, end_idx)
        seg_simple = highs[lowest_idx:search_end_simple + 1]
        peak_offset = int(np.argmax(seg_simple))
        local_high_idx = [lowest_idx + peak_offset]
        print(f"  Simple: idx={local_high_idx[0]}, price={highs[local_high_idx[0]]:.2f}")

    # Now test the engine's _scan_for_impulse
    print("\n--- Engine scan ---")
    engine = RIBEngine()
    result = engine._scan_for_impulse(df, end_idx)
    print(f"Result: {'Found' if result else 'None'}")

    # If None, trace why
    if result is None:
        # Manually trace
        min_return = 0.15
        candidates = []
        for peak_idx in local_high_idx:
            if peak_idx <= lowest_idx:
                continue
            peak_price = highs[peak_idx]
            ret = (peak_price - lowest_price) / lowest_price
            days = peak_idx - lowest_idx
            print(f"\nPeak {peak_idx}: ret={ret*100:.1f}%, days={days}")

            if ret < min_return:
                print("  FAIL: ret too low")
                continue
            if days < 3 or days > 60:
                print("  FAIL: days out of range")
                continue

            # Build candidate manually
            c = engine._build_impulse_candidate(df, lowest_idx, peak_idx, ret, days, lows, highs, vols)
            if c is None:
                print("  FAIL: _build_impulse_candidate returned None")
                # Check why
                avg_vol = np.mean(vols[lowest_idx:peak_idx + 1])
                vol_ma20 = df["vol_ma20"].values
                baseline = float(vol_ma20[lowest_idx]) if not np.isnan(vol_ma20[lowest_idx]) else 0
                vr = avg_vol / baseline if baseline > 0 else 0
                print(f"    vol_ratio={vr:.2f}, avg={avg_vol:.0f}, baseline={baseline:.0f}")
            else:
                print(f"  OK: vol_ratio={c.volume_ratio:.2f}, confirmed={c.is_reversal_confirmed}")
                candidates.append(c)

        print(f"\nTotal candidates: {len(candidates)}")


if __name__ == '__main__':
    main()
