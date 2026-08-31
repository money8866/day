# -*- coding: utf-8 -*-
"""排查：CSV code 格式 vs frames 键"""
import sys
sys.path.insert(0, r"D:\mystock\solo")
import pandas as pd
from w7_second_wave_engine import CacheReader

CSV = r"D:\mystock\solo\report_daily\w7_backtest_v41_signals.csv"
S = pd.read_csv(CSV)
print("CSV code 样例:", S.code.head(3).tolist(), "dtype:", S.code.dtype)
print("CSV event_date 样例:", S.event_date.head(3).tolist())
print("CSV signal_date 样例:", S.signal_date.head(3).tolist())

reader = CacheReader()
n = reader.load_all("20260828", codes=sorted(set(S.code)), min_date="20230101", verbose=False)
print("load_all 返回:", n)
keys = list(reader.frames.keys())[:3]
print("frames 键样例:", keys)
k = S.code.iloc[0]
df = reader.frames.get(k)
if df is not None:
    print(f"{k} 行数:", len(df), "trade_date样例:", df.trade_date.astype(str).head(3).tolist())
    print("event_date in dts:", S.event_date.iloc[0] in df.trade_date.astype(str).tolist())
reader.close()
