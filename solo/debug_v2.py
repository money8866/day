# -*- coding: utf-8 -*-
"""调试 v2 数据 impulse 检测"""
import sys
sys.path.insert(0, '.')

import numpy as np
from test_rib_v2 import create_rib_pattern
from rib.indicators import enrich
from rib.engine import RIBEngine


def main():
    df = enrich(create_rib_pattern())
    engine = RIBEngine()
    end_idx = len(df) - 1

    imp = engine._scan_for_impulse(df, end_idx)
    print(f"Impulse: is={imp.is_impulse}, low_idx={imp.impulse_low_idx}, "
          f"high_idx={imp.impulse_high_idx}, high={imp.impulse_high:.2f}, "
          f"days={imp.impulse_days}, ret={imp.impulse_return*100:.1f}%")

    # 打印关键位置
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    for i in range(95, 152):
        print(f"  day {i}: close={closes[i]:6.2f} high={highs[i]:6.2f} low={lows[i]:6.2f}")


if __name__ == '__main__':
    main()