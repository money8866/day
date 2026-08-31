# -*- coding: utf-8 -*-
"""查益诺思最近60日天量事件与 runup 剔除逻辑"""
import sys
sys.path.insert(0, r"D:\mystock\solo")
import numpy as np
from w7_second_wave_engine import CacheReader, extreme_event, MAX_EVENT_AGE, MIN_BARS

reader = CacheReader()
df = reader.bars_sql("688710.SH", "20260828")
dts = df.trade_date.astype(str).tolist()
c = df.close.to_numpy(dtype=float)

cands = []
for i in range(max(MIN_BARS, len(df) - MAX_EVENT_AGE - 1), len(df) - 2):
    ok, ep = extreme_event(df, i)
    if ok:
        cands.append((i, ep, dts[i]))
print("最近60日天量候选:", [(d, e, c[i]) for i, e, d in cands])

if cands:
    i, ep, d = cands[-1]
    rb = c[i] / c[i - 20] - 1 if i >= 20 else 0.0
    ra = c[-1] / c[i] - 1
    print(f"最后事件 {d} idx={i} close={c[i]:.2f}")
    print(f"  runup_before(前20日)={rb*100:+.1f}%  (>30%剔除: {rb>0.30})")
    print(f"  runup_after(天量后)={ra*100:+.1f}%  (>40%剔除: {ra>0.40})")
    print(f"  当前 close={c[-1]:.2f}")
reader.close()
