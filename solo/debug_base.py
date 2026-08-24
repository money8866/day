# -*- coding: utf-8 -*-
"""调试：恒宝股份"平台5日"的推导过程。"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_hengbao_latest import parse_mcp_file
from rib.engine import RIBEngine

OLD = r"C:\Users\kongx\AppData\Local\Temp\trae\toolcall-output\57717718-522b-4a93-9b9f-faedb0dfc352.txt"
NEW = r"C:\Users\kongx\AppData\Local\Temp\trae\toolcall-output\3a520e23-c490-4364-886f-06de27e8a967.txt"

df = pd.concat([parse_mcp_file(OLD), parse_mcp_file(NEW)]).drop_duplicates("trade_date", keep="last").sort_values("trade_date").reset_index(drop=True)

engine = RIBEngine()
result = engine.analyze(df, ts_code="002104.SZ", name="恒宝股份")
base = result.base
peak = result.peak
imp = result.impulse

print(f"State: {result.state}")
print(f"第一波: {imp.impulse_start_idx}-{imp.impulse_high_idx}  impulse_high={imp.impulse_high:.2f}")
print(f"峰值: idx={peak.peak_idx} 日期={df['trade_date'].values[peak.peak_idx]}  price={peak.peak_price:.2f}")
print(f"平台: start_idx={base.platform_start_idx} end_idx={base.platform_end_idx} days={base.platform_days}")
print(f"  日期: {df['trade_date'].values[base.platform_start_idx]} ~ {df['trade_date'].values[base.platform_end_idx]}")
print(f"  质量分: {base.score:.1f}  base_low={base.low_structure}  base_type={base.base_type}")
print()

# 打印平台窗口逐日明细
start = base.platform_start_idx
end = base.platform_end_idx
print("平台窗口逐日明细:")
print(f"{'日期':<12}{'开盘':>8}{'最高':>8}{'最低':>8}{'收盘':>8}{'量(万手)':>10}")
for i in range(start, end + 1):
    r = df.iloc[i]
    print(f"{r['trade_date']:<12}{r['open']:>8.2f}{r['high']:>8.2f}{r['low']:>8.2f}{r['close']:>8.2f}{r['vol']/1e4:>10.0f}")

# 平台起点判定：峰值后回撤>=3%的第一个低点
print(f"\n平台起点判定(峰值后回撤>=3%的第一个低点): 峰值价={peak.peak_price:.2f}")
for i in range(peak.peak_idx + 1, min(peak.peak_idx + 9, len(df))):
    drop = (peak.peak_price - df['low'].values[i]) / peak.peak_price
    mark = " <-- 平台起点" if i == base.platform_start_idx else ""
    print(f"  {df['trade_date'].values[i]}  低点={df['low'].values[i]:.2f}  回撤={drop*100:.1f}%{mark}")

# 平台终点硬边界：放量(量比>=1.3)且收盘>impulse_high 的第一根K线
print(f"\n平台终点硬边界(放量收盘破第一波高点前一日): impulse_high={imp.impulse_high:.2f}")
for i in range(base.platform_start_idx, min(base.platform_start_idx + 20, len(df))):
    close_i = df['close'].values[i]
    vol_ma = df['vol_ma20'].values[i] if 'vol_ma20' in df.columns else 0
    vol_r = df['vol'].values[i] / vol_ma if vol_ma > 0 else 0
    hit = close_i > imp.impulse_high and vol_r >= 1.3
    mark = " <-- 突破K线(平台硬终点前一日)" if hit else ""
    print(f"  {df['trade_date'].values[i]}  收盘={close_i:.2f} 量比={vol_r:.2f} {'破高' if close_i > imp.impulse_high else '未破'}{mark}")

# ── 复现质量分组件 ──
from rib.indicators import compute_pullback_depth, compute_retain_ratio
import numpy as np
s = base.platform_start_idx
e = base.platform_end_idx
highs = df["high"].values.astype(float)
lows = df["low"].values.astype(float)
closes = df["close"].values.astype(float)
vols = df["vol"].values.astype(float)
impulse_low = imp.impulse_low
base_low = float(np.min(lows[s:e+1]))
base_high = float(np.max(highs[s:e+1]))
base_range = (base_high - base_low) / imp.impulse_high
depth = compute_pullback_depth(imp.impulse_high, base_low, impulse_low)
retain = compute_retain_ratio(base_low, impulse_low, imp.impulse_high)
avg_base_vol = np.mean(vols[s:e+1])
avg_impulse_vol = np.mean(vols[imp.impulse_low_idx:imp.impulse_high_idx+1])
vol_shrink = avg_base_vol / avg_impulse_vol

print(f"\n── 质量分组件复现 ──")
print(f"impulse_low={impulse_low:.2f}  impulse_range={imp.impulse_high-impulse_low:.2f}")
print(f"base_low={base_low:.2f}  base_high={base_high:.2f}  base_range={base_range*100:.1f}%")
print(f"pullback_depth={depth*100:.1f}%  retain={retain*100:.1f}%  vol_shrink={vol_shrink*100:.1f}%")
print(f"第一波均量={avg_impulse_vol:.0f}  平台均量={avg_base_vol:.0f}")

# 评分（复制 _score_base_quality 逻辑）
cfg = engine.base_detector.cfg
sc = 0.0
# 时间 10
opt_lo, opt_hi = cfg.get("optimal_days_low",7), cfg.get("optimal_days_high",15)
if opt_lo <= base.platform_days <= opt_hi: sc += 10
elif base.platform_days >= cfg.get("min_days",5): sc += 6
else: sc += 2
# 回撤 20
d_lo, d_hi = cfg.get("pullback_optimal_low",.2), cfg.get("pullback_optimal_high",.4)
if d_lo <= depth <= d_hi: sc += 20
elif depth <= cfg.get("pullback_good_high",.5): sc += 14
elif depth <= cfg.get("pullback_danger",.6): sc += 8
# 保留率 20
if retain >= cfg.get("retain_excellent",.7): sc += 20
elif retain >= cfg.get("retain_good",.6): sc += 14
elif retain >= cfg.get("retain_pass",.5): sc += 8
# 缩量 20
vs = cfg.get("volume_shrink_ratio",.7)
if vol_shrink <= vs: sc += 20
elif vol_shrink <= .85: sc += 14
elif vol_shrink <= 1.0: sc += 6
# 结构 20
seg_lows = lows[s:e+1]; seg_highs = highs[s:e+1]
if len(seg_lows)>=3 and np.polyfit(range(len(seg_lows)), seg_lows,1)[0] >= 0: sc += 10
if len(seg_highs)>=3 and np.polyfit(range(len(seg_highs)), seg_highs,1)[0] >= -0.01*np.mean(seg_highs): sc += 10
# 惩罚
if vol_shrink > 1.0: sc -= 15
if retain < 0.40: sc -= 20
print(f"\n质量分复现 = {max(0.0, min(100.0, sc)):.1f}（引擎记录 {base.score:.1f}）")
print(f"平台类型: {base.base_type}  低点结构: {base.low_structure}  高点结构: {base.high_structure}")
