# -*- coding: utf-8 -*-
"""查益诺思 688710.SH 天量(20260717)前的累计涨幅与连涨情况"""
import sys
sys.path.insert(0, r"D:\mystock\solo")
from w7_second_wave_engine import CacheReader, extreme_event, finite

reader = CacheReader()
df = reader.bars_sql("688710.SH", "20260828")
print("总K线数:", len(df))
dts = df.trade_date.astype(str).tolist()
ev = "20260717"
i = dts.index(ev)
print(f"天量日={ev} idx={i}")

# 天量前不同窗口累计涨幅
closes = df.close.to_numpy(dtype=float)
for w in (5, 10, 20, 40, 60, 120):
    j = i - w
    if j >= 0:
        r = closes[i] / closes[j] - 1
        print(f"  {w}日前涨幅: {r*100:+.1f}%")

# 连涨天数（天量前）
up_days = 0
for k in range(i - 1, max(0, i - 30), -1):
    if closes[k] >= closes[k - 1]:
        up_days += 1
    else:
        break
print(f"  天量前连涨天数: {up_days}")

# 20日/60日相对高低位置
import numpy as np
for w in (20, 60, 120):
    lo = df.low.to_numpy(dtype=float)[max(0, i - w):i + 1].min()
    hi = df.high.to_numpy(dtype=float)[max(0, i - w):i + 1].max()
    pos = (closes[i] - lo) / (hi - lo) * 100 if hi > lo else 50
    print(f"  {w}日区间内收盘位置: {pos:.0f}% (区间高={hi:.2f} 区间低={lo:.2f})")

# 天量日及之后的走势（验证是否高位回落）
print("\n天量后走势:")
for k in range(i, min(i + 30, len(df)), 5):
    print(f"  {dts[k]} close={closes[k]:.2f} 相对天量日 {closes[k]/closes[i]-1:+.1%}")
reader.close()
