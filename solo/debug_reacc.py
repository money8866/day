# -*- coding: utf-8 -*-
"""聚焦调试：回踩+再启动检测器逐日条件"""
import sys
sys.path.insert(0, '.')

import numpy as np
from test_rib_v3 import create_rib_pattern_v3
from rib.indicators import enrich
from rib.engine import RIBEngine


def main():
    df = enrich(create_rib_pattern_v3())
    engine = RIBEngine()
    end_idx = len(df) - 1

    imp = engine._scan_for_impulse(df, end_idx)
    print(f"Impulse: low_idx={imp.impulse_low_idx}, high_idx={imp.impulse_high_idx}, "
          f"high={imp.impulse_high:.2f}")

    dt = engine.downtrend_detector.detect(df, imp.impulse_low_idx)
    peak = engine.peak_detector.detect(df, imp, end_idx)
    base = engine.base_detector.detect(df, imp, peak, end_idx)
    print(f"Base: [{base.platform_start_idx}, {base.platform_end_idx}], "
          f"high={base.base_high:.2f}, low={base.base_low:.2f}")

    bo = engine.breakout_detector.detect(df, imp, base, end_idx)
    print(f"Breakout: idx={bo.breakout_idx}, price={bo.breakout_price:.2f}, "
          f"fake={bo.is_fake_breakout}")
    print()

    pb = engine.pullback_detector.detect(df, bo, base, imp, end_idx)
    print(f"Pullback: is={pb.is_pullback}")
    if pb.is_pullback:
        print(f"  start_idx={pb.pullback_start_idx} (峰值), low_idx={pb.pullback_low_idx}")
        print(f"  low={pb.pullback_low:.2f}, days={pb.pullback_days}")
        print(f"  depth={pb.pullback_depth:.3f}, vol_ratio={pb.pullback_volume_ratio:.3f}")
        print(f"  broke_impulse_high={pb.broke_impulse_high}, "
              f"fell_back_to_base={pb.fell_back_to_base}")
        print(f"  support={pb.support_found}, score={pb.score}")
        print()

        # 逐日检查再启动条件
        closes = df["close"].values.astype(float)
        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        vols = df["vol"].values.astype(float)

        print("再启动逐日条件检查 (从 pullback_low_idx+1 开始):")
        print(f"{'day':>4} {'close':>7} {'>ma5':>5} {'>prevH':>6} {'ma5>10':>6} "
              f"{'slope':>6} {'volR':>6} {'loc':>5} {'distATR':>8}")
        # 用突破确认价作为距离基准（与检测器一致）
        breakout_ref = bo.breakout_price if bo.breakout_price > 0 else imp.impulse_high
        for i in range(pb.pullback_low_idx + 1, end_idx + 1):
            close = closes[i]
            ma5 = df['ma5'].values[i]
            ma10 = df['ma10'].values[i]
            prev_high = highs[i - 1]
            vol_ma = df['vol_ma20'].values[i]
            vol_r = vols[i] / vol_ma if vol_ma > 0 else 0
            rng = highs[i] - lows[i]
            loc = (close - lows[i]) / rng if rng > 0 else 0.5
            atr = df['atr20'].values[i]
            dist = abs(close - breakout_ref) / atr if atr > 0 else 999
            ma5_prev = df['ma5'].values[i - 1]
            slope_up = ma5 > ma5_prev

            print(f"{i:>4} {close:>7.2f} {'✓' if close > ma5 else '✗':>5} "
                  f"{'✓' if close > prev_high else '✗':>6} "
                  f"{'✓' if ma5 > ma10 else '✗':>6} "
                  f"{'✓' if slope_up else '✗':>6} "
                  f"{vol_r:>6.2f} {loc:>5.2f} {dist:>8.2f}")

        ra = engine.reacc_detector.detect(df, pb, bo, imp, end_idx)
        print()
        print(f"ReAcc result: is={ra.is_reacceleration}, idx={ra.reacc_idx}")


if __name__ == '__main__':
    main()