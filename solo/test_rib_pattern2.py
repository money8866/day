# -*- coding: utf-8 -*-
"""Test RIB engine with highly volatile synthetic RIB pattern."""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from rib.engine import RIBEngine


def create_rib_pattern():
    """Create synthetic data with clear swings for local extreme detection."""
    np.random.seed(123)
    n = 220
    base_date = datetime(2025, 1, 2)
    dates = [(base_date + timedelta(days=i)).strftime('%Y%m%d') for i in range(n)]

    close = np.zeros(n)
    # Phase 1: Downtrend (days 0-100) with clear swings
    # Create a trend line with alternating swings
    for i in range(100):
        trend = 50 - 32 * i / 100.0  # 50 -> 18
        # Add clear swing pattern (waves)
        wave = 6 * np.sin(2 * np.pi * i / 12)
        close[i] = trend + wave + np.random.normal(0, 0.8)

    # Phase 2: Impulse (days 100-118) 18 -> 30 (+67%)
    for i in range(100, 118):
        progress = (i - 99) / 19.0
        close[i] = close[99] + (30 - close[99]) * progress + np.random.normal(0, 0.4)

    # Phase 3: Base (days 118-140) consolidation around 27-30
    for i in range(118, 140):
        close[i] = 27 + 3 * np.sin(2 * np.pi * (i - 118) / 8) + np.random.normal(0, 0.5)

    # Phase 4: Breakout (days 140-144) break above 30
    for i in range(140, 144):
        progress = (i - 139) / 5.0
        close[i] = 30 + (34 - 30) * progress + np.random.normal(0, 0.3)

    # Phase 5: Pullback (days 144-150) to ~31
    for i in range(144, 151):
        progress = (i - 143) / 8.0
        close[i] = 34 - (34 - 31) * progress + np.random.normal(0, 0.3)

    # Phase 6: Reacc (days 151+) strong new impulse
    for i in range(151, n):
        progress = (i - 150) / (n - 151)
        close[i] = 31 + (45 - 31) * progress + np.random.normal(0, 0.5)

    close = np.maximum(close, 5)

    high = close + np.abs(np.random.normal(0, close * 0.02, n))
    low = close - np.abs(np.random.normal(0, close * 0.02, n))
    open_p = close + np.random.normal(0, close * 0.01, n)
    volume = np.random.uniform(5e6, 2e7, n)

    # Pattern-appropriate volume
    for i in range(100, 118):
        volume[i] *= 3.0
    for i in range(118, 140):
        volume[i] *= 0.4
    for i in range(140, 144):
        volume[i] *= 2.5
    for i in range(144, 151):
        volume[i] *= 0.35
    for i in range(151, n):
        volume[i] *= 1.5

    return pd.DataFrame({
        'trade_date': dates,
        'open': open_p,
        'high': high,
        'low': low,
        'close': close,
        'vol': volume,
        'amount': volume * close,
    })


def debug_detectors(df):
    """Debug the detectors step by step."""
    from rib.detectors import DowntrendDetector
    from rib.indicators import enrich, ma, atr

    data = enrich(df.copy())
    close = data['close'].values.astype(float)
    high = data['high'].values.astype(float)
    low = data['low'].values.astype(float)
    volume = data['vol'].values.astype(float)

    # Debug downtrend
    print("=== Downtrend Debug ===")
    dd = DowntrendDetector()

    # Find local extremes
    maxima = dd._find_local_extremes(high, 'max', 5)
    minima = dd._find_local_extremes(low, 'min', 5)
    print(f"Local maxima (order=5): {len(maxima)}")
    for idx in maxima[:10]:
        print(f"  idx={idx}, price={high[idx]:.2f}")
    print(f"Local minima (order=5): {len(minima)}")
    for idx in minima[:10]:
        print(f"  idx={idx}, price={low[idx]:.2f}")

    # Check structure
    if len(maxima) >= 2 and len(minima) >= 2:
        highs_desc = all(maxima[i+1] > maxima[i] for i in range(len(maxima)-1))
        print(f"Maxima are descending: {highs_desc}")
    if len(minima) >= 2:
        lows_desc = all(minima[i+1] > minima[i] for i in range(len(minima)-1))
        print(f"Minima are descending: {lows_desc}")

    # Try with lower order
    for order in [3, 4, 5, 8, 10]:
        m = dd._find_local_extremes(high, 'max', order)
        n = dd._find_local_extremes(low, 'min', order)
        print(f"  order={order}: maxima={len(m)}, minima={len(n)}")

    # Check MA structure
    ma20 = ma(close, 20)
    ma60 = ma(close, 60)
    print(f"MA20 last: {ma20[-1]:.2f}")
    print(f"MA60 last: {ma60[-1]:.2f}")
    print(f"MA20 < MA60: {ma20[-1] < ma60[-1]}")

    # Check slope
    from numpy import polyfit
    idx_60 = np.arange(60)
    slope_60, _ = polyfit(idx_60, ma60[-60:], 1)
    print(f"MA60 slope (last 60): {slope_60:.4f}")


def main():
    df = create_rib_pattern()
    close = df['close'].values

    print(f'Data: {len(df)} rows')
    print(f'Downtrend: {close[0]:.2f} -> {close[99]:.2f}')
    print(f'Impulse high: {close[100:118].max():.2f}')
    print(f'Base: {close[118]:.2f} -> {close[139]:.2f}')
    print(f'Breakout: {close[140]:.2f} -> {close[143]:.2f}')
    print(f'Pullback low: {close[144:151].min():.2f}')
    print(f'Reacc: {close[151]:.2f} -> {close[-1]:.2f}')
    print()

    # Debug first
    debug_detectors(df)
    print()

    # Run engine
    engine = RIBEngine()
    result = engine.analyze(df, ts_code='RIB.TEST', name='RIBTest', industry='Test')

    print(f'State: {result.state}')
    print(f'Is valid: {result.is_valid}')

    if result.downtrend:
        dt = result.downtrend
        print(f'Downtrend: score={dt.score:.1f}, is_dt={dt.is_downtrend}')
        print(f'  Factors: {dt.factors}')
        print(f'  Highs desc count: {dt.higher_highs}')
        print(f'  Lows desc count: {dt.lower_lows}')
        print(f'  MA20 below MA60 ratio: {dt.ma20_below_ma60_ratio:.2f}')
        print(f'  MA60 slope: {dt.ma60_slope:.4f}')

    if result.impulse:
        imp = result.impulse
        print(f'Impulse: ret={imp.impulse_return*100:.1f}%, days={imp.impulse_days}, vol={imp.volume_ratio:.2f}')

    if result.final_score:
        fs = result.final_score
        print(f'Final: {fs.total:.1f} ({fs.grade})')
        print(f'Scores: dt={fs.s_downtrend_bg:.1f}, imp={fs.s_impulse:.1f}, base={fs.s_post_impulse_base:.1f}')
        print(f'  bo={fs.s_second_breakout:.1f}, pb={fs.s_first_pullback:.1f}, ra={fs.s_re_acceleration:.1f}')
        print(f'PRIMARY BUY: {fs.is_primary_buy}')

    print()
    print('Conclusion:')
    print(result.conclusion[:600])


if __name__ == '__main__':
    main()
