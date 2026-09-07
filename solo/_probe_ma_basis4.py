# -*- coding: utf-8 -*-
"""临时探查 v4：ma_bfq == rolling(close*adj/adj[anchor_k]) ？ 找能复现 stored ma_bfq 的固定锚点。"""
import sqlite3
import pandas as pd
import numpy as np

DB = r"D:\mystock\cache_daily\stock_data.db"
CODES = ["600519.SH", "300308.SZ", "300750.SZ", "000001.SZ", "603186.SH"]

conn = sqlite3.connect(DB)
for code in CODES:
    df = pd.read_sql_query(
        "SELECT trade_date, close, adj_factor, ma_bfq_20 FROM stk_factor_pro "
        "WHERE ts_code=? ORDER BY trade_date", conn, params=(code,)).reset_index(drop=True)
    if len(df) < 400:
        print(f"{code} 样本不足 {len(df)}"); continue
    tail0 = 300
    cand = {}
    for k in [0, tail0, len(df) - 250, len(df) - 60, len(df) - 1]:
        a = df.adj_factor.iloc[k]
        c = (df.close * df.adj_factor / a)
        r = c.rolling(20, min_periods=1).mean()
        md = (df.ma_bfq_20.iloc[tail0:] - r.iloc[tail0:]).abs().max()
        cand[f"anchor@{k}({df.trade_date.iloc[k]},adj={a:.4f})"] = round(float(md), 4)
    # 逐k最小误差
    best_k, best_err = None, None
    for i in range(tail0, len(df)):
        a = df.adj_factor.iloc[i]
        c = df.close * df.adj_factor / a
        r = c.rolling(20, min_periods=1).mean()
        md = (df.ma_bfq_20.iloc[tail0:i + 1] - r.iloc[tail0:i + 1]).abs().max()
        if best_err is None or md < best_err:
            best_err, best_k = md, i
    print(f"{code} 末adj={df.adj_factor.iloc[-1]:.4f}")
    for k_, v_ in cand.items():
        print(f"   {k_}: max|diff|={v_}")
    print(f"   best固定锚点: idx={best_k} date={df.trade_date.iloc[best_k]} err={best_err:.4f}")
conn.close()
print("done")
