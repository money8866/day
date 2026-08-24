# -*- coding: utf-8 -*-
"""卫星化学 002648 详细阶段信息。"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scan_bull_top20 import parse
from rib.engine import RIBEngine

path = r"C:\Users\kongx\AppData\Local\Temp\trae\toolcall-output\49ce3550-4cea-4adb-976f-dc1790ea7e2c.txt"
df = parse(path)
print(f"共{len(df)}根K线  {df.iloc[0]['trade_date']} ~ {df.iloc[-1]['trade_date']}")

engine = RIBEngine()
r = engine.analyze(df, ts_code="002648.SZ", name="卫星化学")
imp, peak, base, bo = r.impulse, r.peak, r.base, r.breakout

print(f"\nState: {r.state}")
print(f"第一波: {imp.impulse_start_idx}({df['trade_date'].values[imp.impulse_start_idx]}) "
      f"~ {imp.impulse_high_idx}({df['trade_date'].values[imp.impulse_high_idx]})  "
      f"high={imp.impulse_high:.2f} low={imp.impulse_low:.2f} 涨幅={imp.impulse_return*100:.1f}%")
print(f"峰值: {df['trade_date'].values[peak.peak_idx]}  {peak.peak_price:.2f}")
print(f"平台: {df['trade_date'].values[base.platform_start_idx]} ~ {df['trade_date'].values[base.platform_end_idx]} "
      f"{base.platform_days}日  质量{base.score:.0f} 保留{base.retain_ratio*100:.0f}% 回撤{base.pullback_depth*100:.1f}%")
print(f"突破: is_breakout={bo.is_breakout}")
if bo.is_breakout:
    bd = df['trade_date'].values[bo.breakout_idx] if hasattr(bo, 'breakout_idx') and bo.breakout_idx is not None else '?'
    print(f"  突破日={bd} 价格={bo.breakout_price:.2f} 量比={bo.volume_ratio:.2f} "
          f"收盘位置={getattr(bo,'close_location',float('nan')):.2f} 假突破={bo.is_fake_breakout}")
print(f"结论: {r.conclusion[:120]}")

# 8月逐日：卫星化学状态变化
print("\n8月逐日状态:")
for d in df[df["trade_date"].str.startswith("202608")]["trade_date"].tolist():
    sub = df[df["trade_date"] <= d].reset_index(drop=True)
    rr = engine.analyze(sub, ts_code="002648.SZ", name="卫星化学")
    last = sub.iloc[-1]
    print(f"  {d}  收{last['close']:>8.2f}  {rr.state}")
