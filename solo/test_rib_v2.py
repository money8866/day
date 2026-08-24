# -*- coding: utf-8 -*-
"""Test RIB engine with synthetic data containing the RIB pattern."""
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
    for i in range(100):
        trend = 50 - 32 * i / 100.0
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

    # ── 真实A股式 OHLC 生成（阳线收高位）──
    high = np.zeros(n)
    low = np.zeros(n)
    open_p = np.zeros(n)
    prev_close = close[0]
    for i in range(n):
        c = close[i]
        rng = max(abs(c - prev_close) * 0.8, c * 0.012)
        if c >= prev_close:
            open_p[i] = c - rng * np.random.uniform(0.6, 0.95)
            high[i] = c + rng * np.random.uniform(0.05, 0.15)
            low[i] = open_p[i] - rng * np.random.uniform(0.0, 0.15)
        else:
            open_p[i] = c + rng * np.random.uniform(0.6, 0.95)
            high[i] = open_p[i] + rng * np.random.uniform(0.0, 0.15)
            low[i] = c - rng * np.random.uniform(0.05, 0.15)
        high[i] = max(high[i], c, open_p[i])
        low[i] = min(low[i], c, open_p[i])
        prev_close = c

    volume = np.random.uniform(5e6, 2e7, n)

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

    engine = RIBEngine()
    result = engine.analyze(df, ts_code='RIB.TEST', name='RIBTest', industry='Test')

    print(f'State: {result.state}')
    print(f'Is valid: {result.is_valid}')

    if result.downtrend:
        dt = result.downtrend
        print(f'Downtrend: score={dt.score:.1f}, is_dt={dt.is_downtrend}')
        print(f'  Factors: highs_desc={dt.higher_highs}, lows_desc={dt.lower_lows}')
        print(f'  MA20<MA60 ratio: {dt.ma20_below_ma60_ratio:.2f}')
        print(f'  MA60 slope: {dt.ma60_slope:.4f}')
        print(f'  60d decline: {dt.decline_60d*100:.1f}%')
        print(f'  120d decline: {dt.decline_120d*100:.1f}%')
        print(f'  Duration: {dt.duration_days} days')

    if result.impulse:
        imp = result.impulse
        print(f'Impulse: ret={imp.impulse_return*100:.1f}%, days={imp.impulse_days}, vol={imp.volume_ratio:.2f}')
        print(f'  Break MA20={imp.broke_ma20}, Break MA60={imp.broke_ma60}, Break trend={imp.broke_trend_line}')
        print(f'  Confirmed={imp.is_reversal_confirmed}')

    if result.base:
        base = result.base
        print(f'Base: days={base.platform_days}, retain={base.retain_ratio*100:.1f}%, shrink={base.volume_shrink_ratio:.2f}')
        print(f'  Type={base.base_type}, MA20 slope={base.ma20_slope:.4f}')

    if result.breakout:
        bo = result.breakout
        print(f'Breakout: price={bo.breakout_price:.2f}, vol={bo.volume_ratio:.2f}, close_loc={bo.close_location:.2f}')

    if result.pullback:
        pb = result.pullback
        print(f'Pullback: depth={pb.pullback_depth*100:.1f}%, vol_ratio={pb.pullback_volume_ratio:.2f}')

    if result.reacc:
        ra = result.reacc
        print(f'Reacc: price={ra.reacc_price:.2f}, vol={ra.volume_ratio:.2f}')

    if result.final_score:
        fs = result.final_score
        print(f'Final: {fs.total:.1f} ({fs.grade})')
        print(f'Scores: dt_bg={fs.s_downtrend_bg:.1f}, imp={fs.s_impulse:.1f}, base={fs.s_post_impulse_base:.1f}')
        print(f'  bo={fs.s_second_breakout:.1f}, pb={fs.s_first_pullback:.1f}, ra={fs.s_re_acceleration:.1f}')
        print(f'PRIMARY BUY: {fs.is_primary_buy}')

    if result.trade_plan:
        tp = result.trade_plan
        print(f'Trade plan: buy={tp.buy_price:.2f}, sl={tp.stop_loss:.2f}, RR={result.risk_reward:.1f}')

    if result.veto_triggered:
        print(f'VETO: {result.veto_triggered}')

    print()
    print('Conclusion:')
    print(result.conclusion[:800])


if __name__ == '__main__':
    main()
