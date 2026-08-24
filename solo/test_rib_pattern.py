# -*- coding: utf-8 -*-
"""Test RIB engine with synthetic data containing the RIB pattern."""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from rib.engine import RIBEngine


def create_rib_pattern():
    """Create synthetic data with a clear RIB pattern."""
    np.random.seed(123)
    n = 220
    base_date = datetime(2025, 1, 2)
    dates = [(base_date + timedelta(days=i)).strftime('%Y%m%d') for i in range(n)]

    close = np.zeros(n)
    # Phase 1: Steep downtrend (days 0-100) 50 -> 18
    for i in range(100):
        progress = i / 100.0
        close[i] = 50 - 32 * progress + np.random.normal(0, 0.3)

    # Phase 2: Impulse (days 100-115) 18 -> 28 (+55%)
    for i in range(100, 115):
        progress = (i - 99) / 16.0
        close[i] = close[99] + (28 - close[99]) * progress + np.random.normal(0, 0.3)

    # Phase 3: Base (days 115-140) consolidation 26-28
    for i in range(115, 140):
        close[i] = 26 + np.random.normal(0, 0.4)

    # Phase 4: Breakout (days 140-143) break above 28
    for i in range(140, 143):
        progress = (i - 139) / 4.0
        close[i] = 28 + (32 - 28) * progress + np.random.normal(0, 0.3)

    # Phase 5: Pullback (days 143-148) to ~29
    for i in range(143, 148):
        progress = (i - 142) / 6.0
        close[i] = 32 - (32 - 29) * progress + np.random.normal(0, 0.2)

    # Phase 6: Reacc (days 148+) new impulse
    for i in range(148, n):
        progress = (i - 147) / (n - 148)
        close[i] = 29 + (42 - 29) * progress + np.random.normal(0, 0.5)

    close = np.maximum(close, 5)

    high = close + np.abs(np.random.normal(0, close * 0.015, n))
    low = close - np.abs(np.random.normal(0, close * 0.015, n))
    open_p = close + np.random.normal(0, close * 0.008, n)
    volume = np.random.uniform(5e6, 2e7, n)

    # Pattern-appropriate volume
    for i in range(100, 115):
        volume[i] *= 3.0
    for i in range(115, 140):
        volume[i] *= 0.4
    for i in range(140, 143):
        volume[i] *= 2.5
    for i in range(143, 148):
        volume[i] *= 0.35
    for i in range(148, n):
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
    print(f'Downtrend: {close[0]:.2f} -> {close[99]:.2f} ({(close[99]/close[0]-1)*100:.1f}%)')
    imp_max = close[100:115].max()
    print(f'Impulse: {close[99]:.2f} -> {imp_max:.2f}')
    print(f'Base: {close[115]:.2f} -> {close[139]:.2f}')
    print(f'Breakout: {close[140]:.2f} -> {close[142]:.2f}')
    print(f'Pullback low: {close[143:148].min():.2f}')
    print(f'Reacc: {close[148]:.2f} -> {close[-1]:.2f}')
    print()

    engine = RIBEngine()
    result = engine.analyze(df, ts_code='RIB.TEST', name='RIBTest', industry='Test')

    print(f'State: {result.state}')
    print(f'Is valid: {result.is_valid}')

    if result.final_score:
        fs = result.final_score
        print(f'Final score: {fs.total:.1f}')
        print(f'Grade: {fs.grade}')
        print(f'PRIMARY BUY: {fs.is_primary_buy}')
        print()
        print('Score breakdown:')
        print(f'  Downtrend BG: {fs.s_downtrend_bg:.1f}/10')
        print(f'  Impulse: {fs.s_impulse:.1f}/25')
        print(f'  Post-Impulse Base: {fs.s_post_impulse_base:.1f}/30')
        print(f'  2nd Breakout: {fs.s_second_breakout:.1f}/15')
        print(f'  1st Pullback: {fs.s_first_pullback:.1f}/10')
        print(f'  Re-Acceleration: {fs.s_re_acceleration:.1f}/10')
        print(f'  Theme bonus: +{result.theme_bonus:.1f}')
        print()

        # Show component details
        if result.downtrend:
            dt = result.downtrend
            print(f'Downtrend details: score={dt.score:.1f}, factors={dt.factors}')
        if result.impulse:
            imp = result.impulse
            print(f'Impulse details: return={imp.impulse_return*100:.1f}%, days={imp.impulse_days}, vol={imp.volume_ratio:.2f}')
            print(f'  Break MA20={imp.break_ma20}, Break MA60={imp.break_ma60}, Break trend={imp.break_down_trend}')
        if result.base:
            base = result.base
            print(f'Base details: days={base.platform_days}, retain={base.retain_ratio*100:.1f}%, shrink={base.volume_shrink_ratio:.2f}')
            print(f'  Type={base.base_type}, MA20 slope={base.ma20_slope:.4f}')
        if result.breakout:
            bo = result.breakout
            print(f'Breakout details: price={bo.breakout_price:.2f}, vol={bo.volume_ratio:.2f}, close_loc={bo.close_location:.2f}')
        if result.pullback:
            pb = result.pullback
            print(f'Pullback details: depth={pb.pullback_depth*100:.1f}%, vol_ratio={pb.pullback_volume_ratio:.2f}, test_hi={pb.tested_impulse_high}')
        if result.reacc:
            ra = result.reacc
            print(f'Reacc details: price={ra.reacc_price:.2f}, vol={ra.volume_ratio:.2f}')

        if result.trade_plan:
            tp = result.trade_plan
            print(f'Trade plan: buy={tp.buy_price:.2f}, sl={tp.stop_loss:.2f}, target1={tp.target_1:.2f}, RR={result.risk_reward:.1f}')

    if result.veto_triggered:
        print(f'VETO: {result.veto_triggered}')

    print()
    print('Conclusion:')
    print(result.conclusion[:800])


if __name__ == '__main__':
    main()
