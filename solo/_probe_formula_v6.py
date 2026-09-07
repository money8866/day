# -*- coding: utf-8 -*-
"""临时探查 v6：找与 stk_factor_pro.atr_bfq 匹配的 ATR 定义（无除权样本 603186.SH）。"""
import sqlite3
import pandas as pd
import numpy as np

DB = r"D:\mystock\cache_daily\stock_data.db"
conn = sqlite3.connect(DB)
df = pd.read_sql_query(
    "SELECT trade_date, close, high, low, open, atr_bfq FROM stk_factor_pro "
    "WHERE ts_code='603186.SH' AND trade_date>='20230101' ORDER BY trade_date",
    conn).reset_index(drop=True)
h, l, c = df.high, df.low, df.close
pc = c.shift(1)
tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
s = pd.to_numeric(df.atr_bfq, errors="coerce")

def md(x):
    m = pd.concat([s, x], axis=1, keys=["s", "x"]).dropna().iloc[100:]
    return float((m.s - m.x).abs().max()) if len(m) else np.nan

cands = {}
for n in (5, 6, 10, 14, 20, 26, 30):
    cands[f"SMA({n})"] = tr.rolling(n, min_periods=1).mean()
    cands[f"Wilder({n})"] = tr.ewm(alpha=1 / n, adjust=False, min_periods=1).mean()
    cands[f"EWM({n})"] = tr.ewm(span=n, adjust=False, min_periods=1).mean()
    cands[f"TR-SMAEMA? n{n}"] = tr.ewm(alpha=2 / (n + 1), adjust=False, min_periods=1).mean()
# 用前一日atr替代? 与收盘价比值口径
cands["ATR%=tr/close*100 SMA14"] = (tr / c * 100).rolling(14, min_periods=1).mean()
for k, v in cands.items():
    print(f"{k}: max|diff|={md(v):.4f}")
conn.close()
print("done")
