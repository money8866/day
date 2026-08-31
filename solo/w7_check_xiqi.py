# -*- coding: utf-8 -*-
"""查芯碁微装 688630.SH 最近天量事件与 runup"""
import sys
sys.path.insert(0, r"D:\mystock\solo")
import numpy as np
from w7_second_wave_engine import CacheReader, extreme_event, MAX_EVENT_AGE, MIN_BARS

reader = CacheReader()
for code in ("688630.SH", "002841.SZ"):
    df = reader.bars_sql(code, "20260828")
    dts = df.trade_date.astype(str).tolist()
    c = df.close.to_numpy(dtype=float)
    cands = []
    for i in range(max(MIN_BARS, len(df) - MAX_EVENT_AGE - 1), len(df) - 2):
        ok, ep = extreme_event(df, i)
        if ok:
            cands.append((i, dts[i], c[i]))
    print(f"\n{code} 最近60日天量候选:")
    for i, d, cc in cands[-3:]:
        rb = c[i] / c[i - 20] - 1 if i >= 20 else 0.0
        ra = c[-1] / c[i] - 1
        print(f"  {d} idx={i} close={cc:.2f} runup_before={rb*100:+.1f}% runup_after={ra*100:+.1f}%")
    print(f"  当前 close={c[-1]:.2f} date={dts[-1]}")
reader.close()
