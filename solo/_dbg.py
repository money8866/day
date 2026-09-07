# -*- coding: utf-8 -*-
import sqlite3
import numpy as np
import pandas as pd

CACHE = r"D:\mystock\cache_daily\stock_data.db"
con = sqlite3.connect(CACHE)
codes = ("002384.SZ", "300666.SZ", "603986.SH", "300475.SZ",
         "688332.SH", "688381.SH", "600183.SH", "300672.SZ",
         "688525.SH", "603127.SH", "000712.SZ", "600909.SH")
pl = ",".join("?" * len(codes))
d = pd.read_sql(
    f"select ts_code, trade_date, open, high, low, close, pct_chg, vol "
    f"from daily_cache where ts_code in ({pl}) and trade_date>='20251201' "
    f"order by ts_code, trade_date", con, params=list(codes))
con.close()
d["trade_date"] = d["trade_date"].astype(str)

for code, g0 in d.groupby("ts_code"):
    g = g0.sort_values("trade_date").reset_index(drop=True)
    c = g["close"]
    ma5, ma10 = c.rolling(5).mean(), c.rolling(10).mean()
    ma20, ma60 = c.rolling(20).mean(), c.rolling(60).mean()
    h60 = g["high"].rolling(60).max()
    vol5 = g["vol"].rolling(5).mean()
    vol20 = g["vol"].rolling(20).mean()
    low5 = g["low"].rolling(5).min()
    lo10 = g["low"].rolling(10).min()
    hi10 = g["high"].rolling(10).max()
    i = g.index[-1]
    close = float(c.iloc[i])
    s20 = (float(ma20.iloc[i]) / float(ma20.iloc[i - 5]) - 1) * 100
    up20 = (close / float(ma20.iloc[i]) - 1) * 100
    up60 = (close / float(ma60.iloc[i]) - 1) * 100
    dd = (close / float(h60.iloc[i]) - 1) * 100
    low_hi = bool(float(low5.iloc[i]) > float(low5.iloc[i - 5]))
    vr5 = float(vol5.iloc[i]) / float(vol20.iloc[i])
    g5 = (close / float(c.iloc[i - 5]) - 1) * 100
    pct = float(g["pct_chg"].iloc[i])
    print(f"{code} c={close:.1f} s20={s20:.2f} up20={up20:.1f} "
          f"up60={up60:.1f} dd={dd:.1f} low_hi={low_hi} "
          f"lo10={float(lo10.iloc[i]):.1f} hi10={float(hi10.iloc[i]):.1f} "
          f"vr5={vr5:.2f} g5={g5:.1f} pct={pct:.1f}")
