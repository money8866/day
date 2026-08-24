# -*- coding: utf-8 -*-
"""Debug script for RIB downtrend detection."""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from rib.indicators import enrich, find_local_extremes
from rib.detectors import DowntrendDetector


def main():
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

    df = pd.DataFrame({
        'trade_date': dates,
        'open': open_p, 'high': high, 'low': low, 'close': close,
        'vol': volume, 'amount': volume * close,
    })

    # Enrich
    df = enrich(df)
    end_idx = len(df) - 1

    print(f"Data: {len(df)} rows, end_idx={end_idx}")
    print(f"Last close: {close[end_idx]:.2f}")
    print(f"MA60 last: {df['ma60'].values[end_idx]:.2f}")
    print(f"MA60 slope last: {df['ma60_slope'].values[end_idx]:.4f}")
    print(f"MA20 last: {df['ma20'].values[end_idx]:.2f}")
    print(f"MA20 < MA60: {df['ma20'].values[end_idx] < df['ma60'].values[end_idx]}")

    # Check local extremes for downtrend phase
    highs = df['high'].values.astype(float)
    lows = df['low'].values.astype(float)

    search_start = max(0, end_idx - 180)
    search_end = max(search_start + 60, end_idx)
    print(f"\nSearch window: [{search_start}, {search_end}] ({search_end - search_start} bars)")

    for order in [3, 5, 8, 12]:
        h_idx, l_idx = find_local_extremes(highs[search_start:search_end], order)
        l_idx2, _ = find_local_extremes(lows[search_start:search_end], order)
        print(f"  order={order}: highs={len(h_idx)}, lows={len(l_idx2)}")
        for i, hi in enumerate(h_idx[:8]):
            print(f"    high[{i}]: idx={hi}, price={highs[search_start+hi]:.2f}")
        for i, lo in enumerate(l_idx2[:8]):
            print(f"    low[{i}]: idx={lo}, price={lows[search_start+lo]:.2f}")

    # Run detector
    print("\n--- Running DowntrendDetector ---")
    dd = DowntrendDetector()
    result = dd.detect(df, end_idx)
    print(f"is_downtrend: {result.is_downtrend}")
    print(f"score: {result.score:.1f}")
    print(f"higher_highs: {result.higher_highs}")
    print(f"lower_lows: {result.lower_lows}")
    print(f"ma20_below_ma60_ratio: {result.ma20_below_ma60_ratio:.2f}")
    print(f"price_below_ma60_ratio: {result.price_below_ma60_ratio:.2f}")
    print(f"ma60_slope: {result.ma60_slope:.4f}")
    print(f"ma20_slope: {result.ma20_slope:.4f}")
    print(f"decline_60d: {result.decline_60d*100:.1f}%")
    print(f"decline_120d: {result.decline_120d*100:.1f}%")
    print(f"oversold_degree: {result.oversold_degree:.2f}")
    print(f"duration_days: {result.duration_days}")


if __name__ == '__main__':
    main()
