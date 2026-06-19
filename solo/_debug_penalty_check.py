# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '.')
import tushare_quant as tq
import pandas as pd

stocks_to_check = [
    ('000988.SZ', '华工科技', '物理AI'),
    ('002384.SZ', '东山精密', 'PCB电子电路'),
    ('603936.SH', '博敏电子', 'PCB电子电路'),
    ('002463.SZ', '沪电股份', 'PCB电子电路'),
]

print(f"{'='*80}")
print(f"{'股票':<12} {'MA20乖离':<10} {'近5日涨':<10} {'近3日涨':<10} {'距高点':<10} {'振幅':<8} {'量比':<8}")
print(f"{'':<12} {'失败概率':<10} {'整合评分':<10} {'追高惩罚':<10} {'趋势强度':<10} {'位置安全':<10}")
print(f"{'-'*80}")

for ts_code, name, theme in stocks_to_check:
    try:
        df = tq.pro.daily(ts_code=ts_code, start_date='20260520', end_date='20260618')
        if df is None or len(df) < 15:
            continue
        df = df.sort_values('trade_date').reset_index(drop=True)

        C = df['close'].values.astype(float)

        # 指标
        ret_3 = (C[-1] / C[-4] - 1) * 100
        ret_5 = (C[-1] / C[-6] - 1) * 100
        ma20 = C[-20:].mean()
        bias20 = (C[-1] - ma20) / ma20 * 100
        hhv20 = C[-20:].max()
        dist = (hhv20 - C[-1]) / hhv20 * 100
        r3h = max(C[-1], C[-2], C[-3])
        r3l = min(C[-1], C[-2], C[-3])
        range3 = (r3h - r3l) / r3l * 100
        vol5 = df['vol'].tail(5).mean()
        vol_today = df['vol'].iloc[-1]
        vol_ratio = vol_today / vol5

        # 计算评分
        score, rec, details, fp = tq.calc_unified_stock_score(df, ts_code, theme)

        print(f"{name:<12} {bias20:<10.1f} {ret_5:<10.1f} {ret_3:<10.1f} {dist:<10.1f} {range3:<8.1f} {vol_ratio:<8.2f}")
        print(f"{'':<12} {fp:<10.1f} {score:<10.1f} {details.get('追高惩罚', 0):<10.1f} {details.get('趋势强度', 0):<10.1f} {details.get('位置安全性', 0):<10.1f}")
        print()
    except Exception as e:
        print(f"{name}: 错误 - {e}")

print(f"{'='*80}")

