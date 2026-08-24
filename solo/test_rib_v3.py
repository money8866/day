# -*- coding: utf-8 -*-
"""
RIB 引擎测试 V3 - 精确匹配规范§33最优形态的合成数据。

数据结构（180根K线）：
- 长期下跌 (0-120):     50 -> 20，波动下降，量能递减
- 第一波反转 (120-135):  20 -> 29，放量上攻，收盘均在高处
- 高位平台 (135-159):    27-29 缩量横盘
- 突破 (160-163):        放量突破第一波高点
- 回踩 (164-168):        缩量回踩不破第一波高点
- 再启动 (169-179):      放量再涨，MA5重新上穿

OHLC 采用真实A股特征：阳线收盘接近当日高点。
"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta


def create_rib_pattern_v3():
    np.random.seed(456)
    n = 180
    base_date = datetime(2025, 1, 2)
    dates = [(base_date + timedelta(days=i)).strftime('%Y%m%d') for i in range(n)]

    close = np.zeros(n)

    # Phase 1: 长期下跌 (days 0-120) 50 -> 20
    for i in range(120):
        trend = 50 - 30 * i / 120.0
        wave = 3 * np.sin(2 * np.pi * i / 20)
        close[i] = trend + wave + np.random.normal(0, 0.3)

    # Phase 2: 第一波反转 (days 120-135) 20 -> 29
    for i in range(120, 135):
        progress = (i - 119) / 16.0
        close[i] = close[119] + (29 - close[119]) * progress + np.random.normal(0, 0.15)

    # Phase 3: 高位平台 (days 135-159) 27-29 横盘
    for i in range(135, 160):
        close[i] = 28 + 1 * np.sin(2 * np.pi * (i - 135) / 6) + np.random.normal(0, 0.15)

    # Phase 4: 突破 (days 160-163) 29.5 -> 32 放量上攻
    for i in range(160, 164):
        progress = (i - 159) / 4.0
        close[i] = 29.5 + (32.0 - 29.5) * progress + np.random.normal(0, 0.1)

    # Phase 5: 回踩 (days 164-166) 31.4 -> 30.5 缩量，不破第一波高点29.2
    for i in range(164, 167):
        progress = (i - 163) / 3.0
        close[i] = 31.4 - (31.4 - 30.5) * progress + np.random.normal(0, 0.1)

    # Phase 6: 再启动 (days 167+) 31.2 -> 34.5
    for i in range(167, n):
        progress = (i - 166) / (n - 167)
        close[i] = 31.2 + (34.5 - 31.2) * progress + np.random.normal(0, 0.12)

    close = np.maximum(close, 5)

    # ── 真实A股式 OHLC 生成 ──
    # 判断每根K线相对前收盘的方向，决定日内结构
    high = np.zeros(n)
    low = np.zeros(n)
    open_p = np.zeros(n)

    prev_close = close[0]
    for i in range(n):
        c = close[i]
        rng = max(abs(c - prev_close) * 0.8, c * 0.012)  # 日振幅
        if c >= prev_close:
            # 阳线：开在低位，收在高位（收盘位置 >= 0.85）
            open_p[i] = c - rng * np.random.uniform(0.6, 0.95)
            high[i] = c + rng * np.random.uniform(0.05, 0.15)
            low[i] = open_p[i] - rng * np.random.uniform(0.0, 0.15)
        else:
            # 阴线：开在高位，收在低位
            open_p[i] = c + rng * np.random.uniform(0.6, 0.95)
            high[i] = open_p[i] + rng * np.random.uniform(0.0, 0.15)
            low[i] = c - rng * np.random.uniform(0.05, 0.15)
        # 保证 OHLC 有效性
        high[i] = max(high[i], c, open_p[i])
        low[i] = min(low[i], c, open_p[i])
        prev_close = c

    # ── 成交量模式 ──
    volume = np.random.uniform(5e6, 1.2e7, n)
    for i in range(110, 120):
        volume[i] *= 1.2   # 下跌末期量能异常放大（规范§3-7）
    for i in range(120, 135):
        volume[i] *= 2.8   # 第一波放量
    for i in range(135, 160):
        volume[i] *= 0.35  # 平台缩量
    for i in range(160, 164):
        volume[i] *= 2.2   # 突破放量
    for i in range(164, 167):
        volume[i] *= 0.40  # 回踩缩量
    for i in range(167, n):
        volume[i] *= 1.9   # 再启动放量

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
    from rib.indicators import enrich
    from rib.engine import RIBEngine

    df = create_rib_pattern_v3()
    close = df['close'].values

    print(f'Data: {len(df)} rows')
    print(f'Downtrend: {close[0]:.2f} -> {close[119]:.2f}')
    print(f'Impulse: {close[120]:.2f} -> {close[134]:.2f}')
    print(f'Base: {close[135]:.2f} -> {close[159]:.2f}')
    print(f'Breakout: {close[160]:.2f} -> {close[163]:.2f}')
    print(f'Pullback: {close[164]:.2f} -> {close[168]:.2f}')
    print(f'Reacc: {close[169]:.2f} -> {close[-1]:.2f}')

    df = enrich(df)
    engine = RIBEngine()
    result = engine.analyze(df, ts_code='RIB.V3', name='RIBTestV3', industry='Test')

    print(f'\nState: {result.state}')
    print(f'Is valid: {result.is_valid}')

    for attr in ['downtrend_score', 'impulse_score', 'base_score',
                 'breakout_score', 'pullback_score', 'reacceleration_score',
                 'final_score', 'grade', 'primary_buy', 'reason']:
        val = getattr(result, attr, None)
        if val is not None:
            print(f'  {attr}: {val}')

    print(f'\nConclusion: {result.conclusion}')
    print(f'\nState sequence: {result.state_sequence}')


if __name__ == '__main__':
    main()