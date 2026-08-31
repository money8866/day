# -*- coding: utf-8 -*-
"""验证：天量前/后涨幅过大是否预示更差的 T120 收益（用冒烟回测信号CSV）"""
import sys
sys.path.insert(0, r"D:\mystock\solo")
import numpy as np
import pandas as pd
from w7_second_wave_engine import CacheReader

CSV = r"D:\mystock\solo\report_daily\w7_backtest_v41_signals.csv"
S = pd.read_csv(CSV)
S = S.dropna(subset=["fwd120"])
print(f"信号样本={len(S)}")

reader = CacheReader()
codes = sorted(set(S.code))
reader.load_all("20260828", codes=codes, min_date="20230101", verbose=False)
reader.close()

runup_before, runup_after = [], []
for _, r in S.iterrows():
    df = reader.frames.get(r["code"])
    if df is None or len(df) < 30:
        runup_before.append(np.nan); runup_after.append(np.nan); continue
    dts = df.trade_date.astype(str).tolist()
    ev = str(r["event_date"]); sg = str(r["signal_date"])
    try:
        i, j = dts.index(ev), dts.index(sg)
    except ValueError:
        runup_before.append(np.nan); runup_after.append(np.nan); continue
    c = df.close.to_numpy(dtype=float)
    runup_before.append(c[i] / c[i - 20] - 1 if i >= 20 else np.nan)  # 天量前20日涨幅
    runup_after.append(c[j] / c[i] - 1)  # 信号日相对天量日涨幅

S["runup_before"] = runup_before
S["runup_after"] = runup_after

print("\n== 天量前20日涨幅 与 T120 ==")
for lo, hi in ((-1, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.5), (0.5, 5)):
    g = S[(S.runup_before >= lo) & (S.runup_before < hi)].dropna(subset=["fwd120"])
    if len(g) >= 10:
        print(f"  前20日涨幅[{lo*100:+.0f}%,{hi*100:+.0f}%): n={len(g)} T120均值={g.fwd120.mean()*100:+.2f}% ≥30%={(g.fwd120>=0.3).mean()*100:.0f}% ≥50%={(g.fwd120>=0.5).mean()*100:.0f}%")

print("\n== 天量后涨幅（信号日相对天量日）与 T120 ==")
for lo, hi in ((-1, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.5), (0.5, 5)):
    g = S[(S.runup_after >= lo) & (S.runup_after < hi)].dropna(subset=["fwd120"])
    if len(g) >= 10:
        print(f"  天量后涨幅[{lo*100:+.0f}%,{hi*100:+.0f}%): n={len(g)} T120均值={g.fwd120.mean()*100:+.2f}% ≥30%={(g.fwd120>=0.3).mean()*100:.0f}% ≥50%={(g.fwd120>=0.5).mean()*100:.0f}%")

# 剔除规则模拟：前20日涨幅>30% OR 天量后涨幅>40% 剔除
print("\n== 剔除规则模拟（前20日>30% 或 天量后>40%）==")
killed = S[(S.runup_before > 0.30) | (S.runup_after > 0.40)]
kept = S[~((S.runup_before > 0.30) | (S.runup_after > 0.40))]
for lab, g in (("剔除", killed), ("保留", kept)):
    gg = g.dropna(subset=["fwd120"])
    if len(gg):
        print(f"  {lab}: n={len(gg)} T120均值={gg.fwd120.mean()*100:+.2f}% 中位={gg.fwd120.median()*100:+.2f}% ≥30%={(gg.fwd120>=0.3).mean()*100:.0f}% ≥50%={(gg.fwd120>=0.5).mean()*100:.0f}% Top10%率={(gg.fwd120>=gg.fwd120.quantile(0.9)).mean()*100:.0f}%")
