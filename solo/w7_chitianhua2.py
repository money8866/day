# -*- coding: utf-8 -*-
"""赤天化：8/25-8/28 日内细节 + 突破日确认"""
import sys
sys.path.insert(0, r"D:\mystock\solo")
import numpy as np
from w7_second_wave_engine import CacheReader

reader = CacheReader()
df = reader.bars_sql("600227.SH", "20260828")
dts = df.trade_date.astype(str).tolist()
c = df.close.to_numpy(dtype=float)
h = df.high.to_numpy(dtype=float)
l = df.low.to_numpy(dtype=float)
o = df.open.to_numpy(dtype=float)
v = df.vol.to_numpy(dtype=float)
tr = df.turnover_rate_f.fillna(0).to_numpy(dtype=float)
pc = df.pct_chg.fillna(0).to_numpy(dtype=float)

ei = dts.index("20260819")
print("== 天量后每日明细 ==")
for k in range(ei, len(df)):
    vol20 = np.mean(v[max(0, k - 20):k]) if k >= 20 else np.mean(v[:k])
    pos = (c[k] - l[k]) / max(h[k] - l[k], 0.01) * 100
    upper = (h[k] - max(o[k], c[k])) / max(h[k] - l[k], 0.01)
    print(f"{dts[k]} O={o[k]:.2f} H={h[k]:.2f} L={l[k]:.2f} C={c[k]:.2f} 涨跌={pc[k]:+6.2f}% "
          f"vol={v[k]:8.0f} 量比={v[k]/vol20:.2f} 换手={tr[k]:5.2f}% pos={pos:5.1f} 上影={upper:.2f}")

# 前高平台：天量后高点
print(f"\n天量后(8/20-8/27)最高={h[ei+1:len(df)-1].max():.2f}")
print(f"8/28 之前20日最高={h[max(0,len(df)-21):len(df)-1].max():.2f}")
reader.close()
