# -*- coding: utf-8 -*-
"""临时探查 v2：全历史比较 ma_bfq_20 与 close.rolling(20)，确认价格口径 + 一致性。"""
import sqlite3
import pandas as pd
import numpy as np

DB = r"D:\mystock\cache_daily\stock_data.db"
CODES = ["300308.SZ", "000001.SZ", "600519.SH", "300750.SZ", "603186.SH"]

conn = sqlite3.connect(DB)
for code in CODES:
    df = pd.read_sql_query(
        "SELECT trade_date, close, open, high, low, pre_close, ma_bfq_20, ma_bfq_10, ma_bfq_60, "
        "atr_bfq, volume_ratio, turnover_rate FROM stk_factor_pro "
        "WHERE ts_code=? ORDER BY trade_date", conn, params=(code,)).reset_index(drop=True)
    if len(df) < 250:
        print(f"{code} 样本不足 {len(df)}"); continue
    ma20 = df.close.rolling(20, min_periods=1).mean()
    ma10 = df.close.rolling(10, min_periods=1).mean()
    ma60 = df.close.rolling(60, min_periods=1).mean()
    # 只在索引>=59（有完整60日上下文）后比较
    tail = df.iloc[250:]
    d20 = (df.ma_bfq_20.iloc[250:] - ma20.iloc[250:]).abs()
    d10 = (df.ma_bfq_10.iloc[250:] - ma10.iloc[250:]).abs()
    d60 = (df.ma_bfq_60.iloc[250:] - ma60.iloc[250:]).abs()
    rel20 = (d20 / ma20.iloc[250:].replace(0, np.nan)).max()
    # 检查 close 是否等于 pre_close*(1+pct?) —— 是否未复权(raw)：若区间内除权，close将跳空而raw ma跟随
    print(f"{code} n={len(df)} 日期={df.trade_date.iloc[0]}..{df.trade_date.iloc[-1]}")
    print(f"   |stored_ma20 - roll_close20|max={d20.max():.4f}  rel={rel20:.4%}  |ma10|max={d10.max():.4f}  |ma60|max={d60.max():.4f}")
    # 与 daily_cache 的 close 一致性
    dc = pd.read_sql_query("SELECT trade_date, close FROM daily_cache WHERE ts_code=? ORDER BY trade_date",
                           conn, params=(code,)).reset_index(drop=True)
    if len(dc):
        m = dc.merge(df[["trade_date", "close"]].rename(columns={"close": "sf_close"}), on="trade_date", how="inner")
        diff = (m.close - m.sf_close).abs().max()
        print(f"   daily_cache vs stk_factor close 一致性 max|diff|={diff:.6f} ({len(m)}行重叠)")

conn.close()
print("done")
