# -*- coding: utf-8 -*-
"""验证：事件年龄(信号日距天量日天数) 与 T120 的关系"""
import sys
sys.path.insert(0, r"D:\mystock\solo")
import numpy as np
import pandas as pd

CSV = r"D:\mystock\solo\report_daily\w7_backtest_v41_signals.csv"
S = pd.read_csv(CSV)
S = S.dropna(subset=["fwd120"])
S["age"] = S["signal_date"].astype(int) - S["event_date"].astype(int)
# 天数差转为交易日近似（自然日，粗略）
print("信号样本:", len(S))
print("\n== 事件年龄(自然日) 与 T120 ==")
for lo, hi in ((-1, 10), (10, 20), (20, 30), (30, 60), (60, 999)):
    g = S[(S.age >= lo) & (S.age < hi)]
    if len(g) >= 10:
        print(f"  年龄[{lo},{hi})日: n={len(g)} T120均值={g.fwd120.mean()*100:+.2f}% 中位={g.fwd120.median()*100:+.2f}% ≥30%={(g.fwd120>=0.3).mean()*100:.0f}% ≥50%={(g.fwd120>=0.5).mean()*100:.0f}% Top10%率={(g.fwd120>=g.fwd120.quantile(0.9)).mean()*100:.0f}%")
