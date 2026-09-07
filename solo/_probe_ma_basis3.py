# -*- coding: utf-8 -*-
"""临时探查 v3：判断 ma_bfq_* 是基于哪个价格口径（raw/qfq/hfq/adj-归一）计算。"""
import sqlite3
import pandas as pd
import numpy as np

DB = r"D:\mystock\cache_daily\stock_data.db"
CODES = ["600519.SH", "300308.SZ", "300750.SZ", "000001.SZ", "603186.SH"]

conn = sqlite3.connect(DB)
for code in CODES:
    df = pd.read_sql_query(
        "SELECT trade_date, close, close_qfq, close_hfq, adj_factor, ma_bfq_20, ma_qfq_20, ma_hfq_20 "
        "FROM stk_factor_pro WHERE ts_code=? ORDER BY trade_date", conn, params=(code,)).reset_index(drop=True)
    if len(df) < 250:
        print(f"{code} 样本不足 {len(df)}"); continue
    tail = df.iloc[250:]
    def md(a, b):
        return (a.iloc[250:] - b.iloc[250:]).abs().max()
    r_raw = df.close.rolling(20, min_periods=1).mean()
    r_qfq = df.close_qfq.rolling(20, min_periods=1).mean()
    r_hfq = df.close_hfq.rolling(20, min_periods=1).mean()
    # adj 归一（以最新 adj 为锚 => 前复权）：raw * adj / adj_latest
    c_bfq_latest = df.close * df.adj_factor / df.adj_factor.iloc[-1]
    r_latest = c_bfq_latest.rolling(20, min_periods=1).mean()
    print(f"{code} n={len(df)} 末adj={df.adj_factor.iloc[-1]:.4f}")
    print(f"   |ma_bfq-roll_raw|max={md(df.ma_bfq_20, r_raw):.4f}")
    print(f"   |ma_bfq-roll_qfq|max={md(df.ma_bfq_20, r_qfq):.4f}")
    print(f"   |ma_bfq-roll_hfq|max={md(df.ma_bfq_20, r_hfq):.4f}")
    print(f"   |ma_bfq-roll(close*adj/adjust_latest)|max={md(df.ma_bfq_20, r_latest):.4f}")
    print(f"   |ma_bfq-ma_qfq|max={md(df.ma_bfq_20, df.ma_qfq_20):.4f}  |ma_bfq-ma_hfq|max={md(df.ma_bfq_20, df.ma_hfq_20):.4f}")
conn.close()
print("done")
