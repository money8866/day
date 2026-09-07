# -*- coding: utf-8 -*-
"""临时探查 v5：验证 ATR(14,wilder)/RSI(6,wilder)/MACD/updays 本地公式 == stk_factor_pro 存储(无除权样本)。"""
import sqlite3
import pandas as pd
import numpy as np

DB = r"D:\mystock\cache_daily\stock_data.db"
conn = sqlite3.connect(DB)

def wilder_rsi(close, n=6):
    delta = close.diff()
    up = delta.clip(lower=0.0)
    dn = (-delta).clip(lower=0.0)
    up_avg = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn_avg = dn.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up_avg / dn_avg
    out = 100.0 - 100.0 / (1.0 + rs)
    return out.where(dn_avg > 0, 100.0)

for code in ["603186.SH", "300308.SZ"]:
    df = pd.read_sql_query(
        "SELECT trade_date, close, high, low, atr_bfq, rsi_bfq_6, macd_bfq, updays, adj_factor "
        "FROM stk_factor_pro WHERE ts_code=? AND trade_date>='20230101' ORDER BY trade_date",
        conn, params=(code,)).reset_index(drop=True)
    if len(df) < 300:
        print(f"{code} 不足 {len(df)}"); continue
    c = df.close
    pc = c.shift(1)
    tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=1).mean()
    rsi = wilder_rsi(c, 6)
    e12 = c.ewm(span=12, adjust=False).mean()
    e26 = c.ewm(span=26, adjust=False).mean()
    dif = e12 - e26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd = (dif - dea) * 2.0
    up = (c.diff() > 0).astype(int)
    runs = up.groupby((up.diff() != 0).cumsum()).cumcount() + 1
    updays = up * runs
    s = 100
    print(f"{code} n={len(df)} (从 index 100 起比较)")
    for col, calc in [("atr_bfq", atr), ("rsi_bfq_6", rsi), ("macd_bfq", macd), ("updays", updays)]:
        a = pd.to_numeric(df[col], errors="coerce")
        m = pd.concat([a, calc], axis=1, keys=["s", "x"]).dropna()
        m = m.iloc[100:]
        if len(m):
            md = (m.s - m.x).abs().max()
            print(f"   |{col}-local|max={md:.5f} (样本{len(m)})")
conn.close()
print("done")
