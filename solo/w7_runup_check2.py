# -*- coding: utf-8 -*-
"""查益诺思完整走势，看天量前是否有连续上涨段"""
import sys
sys.path.insert(0, r"D:\mystock\solo")
import numpy as np
from w7_second_wave_engine import CacheReader

reader = CacheReader()
df = reader.bars_sql("688710.SH", "20260828")
dts = df.trade_date.astype(str).tolist()
closes = df.close.to_numpy(dtype=float)
highs = df.high.to_numpy(dtype=float)
lows = df.low.to_numpy(dtype=float)
vols = df.vol.to_numpy(dtype=float)
turns = df.turnover_rate_f.fillna(0).to_numpy(dtype=float)

# 按月抽样看走势
print("近4个月走势（每5个交易日）:")
for k in range(len(df) - 90, len(df), 5):
    print(f"  {dts[k]} close={closes[k]:8.2f} 5日涨={(closes[k]/closes[max(0,k-5)]-1)*100:+6.1f}% 量={vols[k]:.0f}")

# 找到天量日
ev = "20260717"
i = dts.index(ev)
print(f"\n天量日={ev} close={closes[i]:.2f} vol={vols[i]:.0f} turn={turns[i]:.2f}%")

# 天量日之前的波段低点：找天量日前 60 天内的最低点，算从低点涨幅
for w in (30, 60, 90):
    lo_i = np.argmin(lows[max(0, i - w):i])
    lo_i += max(0, i - w)
    run = closes[i] / closes[lo_i] - 1
    days = i - lo_i
    print(f"前{w}日内最低点 {dts[lo_i]} close={closes[lo_i]:.2f} → 天量日涨幅 {run*100:+.1f}%（{days}个交易日）")

# 天量日前最高点位置（相对区间）
for w in (30, 60, 90):
    seg = closes[max(0, i - w):i + 1]
    rank = (seg > closes[i]).mean() * 100
    print(f"天量日收盘在近{w}日收盘分布中的分位: {rank:.0f}%")

# 天量日是否高于前高（breakout 位置）
prev_hi = highs[max(0, i - 60):i].max()
print(f"\n天量日前60日最高={prev_hi:.2f} 天量日高={highs[i]:.2f} → 是否创新高={highs[i] > prev_hi}")
reader.close()
