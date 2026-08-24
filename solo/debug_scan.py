# -*- coding: utf-8 -*-
"""Debug the _scan_for_impulse method."""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from rib.indicators import enrich, find_local_extremes
from rib.engine import RIBEngine


def create_rib_pattern():
    np.random.seed(123)
    n = 220
    base_date = datetime(2025, 1, 2)
    dates = [(base_date + timedelta(days=i)).strftime('%Y%m%d') for i in range(n)]
    close = np.zeros(n)
    for i in range(100):
        trend = 50 - 32 * i / 100.0
        wave = 6 * np.sin(2 * np.pi * i / 12)
        close[i] = trend + wave + np.random.normal(0, 0.8)
    for i in range(100, 118):
        progress = (i - 99) / 19.0
        close[i] = close[99] + (30 - close[99]) * progress + np.random.normal(0, 0.4)
    for i in range(118, 140):
        close[i] = 27 + 3 * np.sin(2 * np.pi * (i - 118) / 8) + np.random.normal(0, 0.5)
    for i in range(140, 144):
        progress = (i - 139) / 5.0
        close[i] = 30 + (34 - 30) * progress + np.random.normal(0, 0.3)
    for i in range(144, 151):
        progress = (i - 143) / 8.0
        close[i] = 34 - (34 - 31) * progress + np.random.normal(0, 0.3)
    for i in range(151, n):
        progress = (i - 150) / (n - 151)
        close[i] = 31 + (45 - 31) * progress + np.random.normal(0, 0.5)
    close = np.maximum(close, 5)
    high = close + np.abs(np.random.normal(0, close * 0.02, n))
    low = close - np.abs(np.random.normal(0, close * 0.02, n))
    open_p = close + np.random.normal(0, close * 0.01, n)
    volume = np.random.uniform(5e6, 2e7, n)
    for i in range(100, 118): volume[i] *= 3.0
    for i in range(118, 140): volume[i] *= 0.4
    for i in range(140, 144): volume[i] *= 2.5
    for i in range(144, 151): volume[i] *= 0.35
    for i in range(151, n): volume[i] *= 1.5
    return pd.DataFrame({
        'trade_date': dates, 'open': open_p, 'high': high, 'low': low,
        'close': close, 'vol': volume, 'amount': volume * close,
    })


def main():
    df = create_rib_pattern()
    df = enrich(df)
    end_idx = len(df) - 1

    highs = df["high"].values.astype(float)
    lows = df["low"].values.astype(float)
    closes = df["close"].values.astype(float)
    vols = df["vol"].values.astype(float)

    print(f"Data: {len(df)} rows, end_idx={end_idx}")

    # Check what _scan_for_impulse sees
    cfg = {"min_return": 0.15, "max_days": 150}
    min_return = cfg.get("min_return", 0.15)
    max_lookback = cfg.get("max_days", 150)

    scan_start = max(60, end_idx - max_lookback)
    print(f"Scan range: [{scan_start}, {end_idx}] ({end_idx - scan_start} bars)")

    # Find local lows
    _, low_indices = find_local_extremes(lows[scan_start:end_idx+1], order=5)
    print(f"Local lows (order=5): {len(low_indices)}")
    low_indices = low_indices + scan_start

    # If none, try order=3
    if len(low_indices) == 0:
        _, low_indices = find_local_extremes(lows[scan_start:end_idx+1], order=3)
        print(f"Local lows (order=3): {len(low_indices)}")
        low_indices = low_indices + scan_start

    for li in low_indices:
        print(f"  Low idx={li}, price={lows[li]:.2f}")

    # Check each candidate
    candidates = []
    for low_idx in low_indices:
        if low_idx + 3 > end_idx:
            print(f"  Skip idx={low_idx}: too close to end")
            continue

        low_price = lows[low_idx]
        seg_highs = highs[low_idx:end_idx + 1]
        high_offset = int(np.argmax(seg_highs))
        high_idx = low_idx + high_offset
        high_price = seg_highs.max()

        ret = (high_price - low_price) / low_price
        days = high_idx - low_idx

        print(f"  Candidate: low={low_idx}({low_price:.2f}), high={high_idx}({high_price:.2f}), ret={ret*100:.1f}%, days={days}")

        if ret < min_return:
            print(f"    FAIL: ret {ret*100:.1f}% < {min_return*100:.0f}%")
            continue

        if days < 3 or days > 60:
            print(f"    FAIL: days {days} out of range [3,60]")
            continue

        # Check MA breakouts
        has_ma20 = "ma20" in df.columns
        has_ma60 = "ma60" in df.columns
        break_ma20 = False
        break_ma60 = False
        break_trend = False

        if has_ma20 and has_ma60:
            ma20_at_low = float(df["ma20"].values[low_idx]) if not np.isnan(df["ma20"].values[low_idx]) else low_price
            ma60_at_low = float(df["ma60"].values[low_idx]) if not np.isnan(df["ma60"].values[low_idx]) else low_price
            print(f"    MA20@low={ma20_at_low:.2f}, MA60@low={ma60_at_low:.2f}")
            print(f"    high_price={high_price:.2f}")

            if high_price > ma20_at_low:
                break_ma20 = True
                print(f"    BREAK MA20!")
            if high_price > ma60_at_low:
                break_ma60 = True
                print(f"    BREAK MA60!")

            prev_highs = highs[max(0, low_idx - 60):low_idx]
            if len(prev_highs) > 10:
                x = np.arange(len(prev_highs))
                slope, intercept = np.polyfit(x, prev_highs, 1)
                if slope < 0:
                    trend_line = slope * len(prev_highs) + intercept
                    print(f"    Trend slope={slope:.4f}, trend_line={trend_line:.2f}")
                    if high_price > trend_line:
                        break_trend = True
                        print(f"    BREAK TREND!")

        is_confirmed = break_ma20 or break_ma60 or break_trend
        print(f"    Confirmed: {is_confirmed} (ma20={break_ma20}, ma60={break_ma60}, trend={break_trend})")

        # Volume
        impulse_vols = vols[low_idx:high_idx + 1]
        vol_ma20_arr = df["vol_ma20"].values
        baseline_vol = float(vol_ma20_arr[low_idx]) if not np.isnan(vol_ma20_arr[low_idx]) else np.mean(vols[max(0, low_idx - 20):low_idx])
        avg_impulse_vol = np.mean(impulse_vols)
        vol_ratio = avg_impulse_vol / baseline_vol if baseline_vol > 0 else 0
        print(f"    Vol ratio: {vol_ratio:.2f} (baseline={baseline_vol:.0f}, avg={avg_impulse_vol:.0f})")

        candidates.append({
            "low_idx": low_idx, "high_idx": high_idx,
            "ret": ret, "days": days, "confirmed": is_confirmed,
        })

    print(f"\nTotal candidates: {len(candidates)}")
    confirmed = [c for c in candidates if c["confirmed"]]
    print(f"Confirmed candidates: {len(confirmed)}")

    if confirmed:
        confirmed.sort(key=lambda c: c["low_idx"], reverse=True)
        best = confirmed[0]
        print(f"Best: low={best['low_idx']}, high={best['high_idx']}, ret={best['ret']*100:.1f}%, days={best['days']}")
    elif candidates:
        candidates.sort(key=lambda c: c["ret"], reverse=True)
        best = candidates[0]
        print(f"Best (unconfirmed): low={best['low_idx']}, high={best['high_idx']}, ret={best['ret']*100:.1f}%")
    else:
        print("NO candidates found!")

        # Try scanning from the known impulse start point
        print("\n--- Direct check of known impulse (idx ~99-117) ---")
        low_99 = lows[99]
        high_117 = highs[100:118].max()
        ret_known = (high_117 - low_99) / low_99
        days_known = 117 - 99
        print(f"  low[99]={low_99:.2f}, high={high_117:.2f}, ret={ret_known*100:.1f}%, days={days_known}")

        # Check why it's not found
        # The issue: find_local_extremes with order=5 may not find idx=99 as a local low
        # because the price before it might be even lower
        print("\n  Checking local minima around idx 99...")
        for offset in range(-20, 5):
            idx = 99 + offset
            if 0 <= idx < len(lows):
                segment = lows[max(0, idx-5):idx+6]
                is_min = lows[idx] == min(segment)
                print(f"    idx={idx}, low={lows[idx]:.2f}, is_local_min={is_min}")


if __name__ == '__main__':
    main()
