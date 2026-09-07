# -*- coding: utf-8 -*-
"""临时探查：stk_factor_pro 的 ma_bfq_20 / atr_bfq / rsi_bfq_6 / updays 与
daily_cache 未复权 close 本地计算是否一致（决定迁移口径）。"""
import sqlite3
import pandas as pd
import numpy as np

DB = r"D:\mystock\cache_daily\stock_data.db"
CODES = ["300308.SZ", "603186.SH", "000001.SZ", "600519.SH", "002594.SZ", "300750.SZ"]

conn = sqlite3.connect(DB)

def wilder_rsi(close, n=6):
    delta = close.diff()
    up = delta.clip(lower=0.0)
    dn = -delta.clip(upper=0.0)
    # 初值用简单均值,后 Wilder 平滑
    r = pd.Series(np.nan, index=close.index)
    if len(close) <= n:
        return r
    up_avg = up.iloc[1:n + 1].mean()
    dn_avg = dn.iloc[1:n + 1].mean()
    if dn_avg == 0:
        r.iloc[n] = 100.0
    else:
        r.iloc[n] = 100.0 - 100.0 / (1.0 + up_avg / dn_avg)
    for i in range(n + 1, len(close)):
        up_avg = (up_avg * (n - 1) + up.iloc[i]) / n
        dn_avg = (dn_avg * (n - 1) + dn.iloc[i]) / n
        if dn_avg == 0:
            r.iloc[i] = 100.0
        else:
            r.iloc[i] = 100.0 - 100.0 / (1.0 + up_avg / dn_avg)
    return r

def true_range(h, l, pc):
    pc = pc.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr

for code in CODES:
    df = pd.read_sql_query(
        "SELECT trade_date, close, high, low, ma_bfq_20, ma_bfq_10, atr_bfq, rsi_bfq_6, updays "
        "FROM stk_factor_pro WHERE ts_code=? AND trade_date>='20260801' ORDER BY trade_date LIMIT 30",
        conn, params=(code,))
    if df.empty:
        continue
    df = df.reset_index(drop=True)
    ma20 = df.close.rolling(20, min_periods=1).mean()
    ma10 = df.close.rolling(10, min_periods=1).mean()
    # ATR 常见 14 日 (Wilder)
    tr = true_range(df.high, df.low, df.close)
    atr = tr.ewm(alpha=1 / 14, min_periods=1).mean()
    rsi = wilder_rsi(df.close, 6)
    dif20 = (df.ma_bfq_20 - ma20).abs().max()
    dif10 = (df.ma_bfq_10 - ma10).abs().max()
    dif_atr = (df.atr_bfq.dropna() - atr.reindex(df.atr_bfq.dropna().index)).abs().max() if df.atr_bfq.notna().any() else np.nan
    dif_rsi = (df.rsi_bfq_6.dropna() - rsi.reindex(df.rsi_bfq_6.dropna().index)).abs().max() if df.rsi_bfq_6.notna().any() else np.nan
    print(f"{code} 样本{len(df)}d | |ma20_stored-roll_close|max={dif20:.4f} | |ma10|max={dif10:.4f} | |atr_stored-wilder14|max={dif_atr:.4f} | |rsi6|max={dif_rsi:.4f}")
    print(f"   首行: close={df.close.iloc[0]:.2f} ma20={df.ma_bfq_20.iloc[0]:.4f} roll20={ma20.iloc[0]:.4f}  updays样例={df.updays.tolist()[:6]}")

conn.close()
print("done")
